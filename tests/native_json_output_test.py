#!/usr/bin/env python3
"""Opaque contracts for native schema-v1 JSON Lines output."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

try:
    import jsonschema
except ImportError:
    print("SKIP: python jsonschema package is unavailable")
    sys.exit(77)

from validate_output_schema import semantic_errors


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "docs/output-v1.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
IWYU = Path(sys.argv[1]).resolve()


def run(source, *iwyu_args):
    command = [str(IWYU)]
    for argument in iwyu_args:
        command.extend(["-Xiwyu", argument])
    command.extend(["-I", ".", "-std=c++17", source])
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True)


def records(result):
    parsed = []
    for line in result.stdout.splitlines():
        assert line.strip(), "structured stdout contains an empty line"
        record = json.loads(line)
        VALIDATOR.validate(record)
        errors = semantic_errors(record)
        assert not errors, errors
        parsed.append(record)
    assert parsed, "structured stdout contains no records"
    return parsed


def by_path(values, suffix):
    return next(value for value in values
                if value["file"]["path"].endswith(suffix))


def command_digest(directory, source_file, arguments):
    canonical = json.dumps(
        {"directory": directory, "file": source_file,
         "arguments": arguments},
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def main():
    # The default remains legacy human-readable output on stderr.
    text = run("tests/cxx/ctad.cc")
    assert text.returncode == 0, text.stderr
    assert text.stdout == ""
    assert "should add these lines:" in text.stderr
    assert "The full include-list" in text.stderr

    # Direct JSON mode emits a stable index-zero identity and applied edits.
    first = run("tests/cxx/ctad.cc", "--output_format=json")
    second = run("tests/cxx/ctad.cc", "--output_format=json")
    assert first.returncode == second.returncode == 0
    changed_records = records(first)
    assert first.stdout == second.stdout
    changed = by_path(changed_records, "tests/cxx/ctad.cc")
    ordinary_add = next(op for op in changed["operations"]
                        if op["subject_action"] == "add")
    assert ordinary_add["provider"]["selected"] == {
        "status": "unavailable", "value": None}
    assert ordinary_add["provider"]["candidates"] == {
        "status": "unavailable", "value": None}
    assert changed["status"] == "changes"
    assert changed["invocation"]["index"] == 0
    assert changed["invocation"]["id"].startswith("inv-000000-")
    assert changed["desired_state"]["complete"] is True
    assert changed["operations"]
    assert all(op["disposition"] == "apply" and
               op["subject_action"] in ("add", "remove")
               for op in changed["operations"])
    assert "should add these lines:" not in first.stdout

    with tempfile.NamedTemporaryFile(mode="w", suffix=".rsp") as response:
        response.write("-I . -std=c++17 tests/cxx/ctad.cc\n")
        response.flush()
        response_result = subprocess.run(
            [str(IWYU), "-Xiwyu", "--output_format=json", "@" + response.name],
            cwd=ROOT, text=True, capture_output=True)
    response_record = by_path(records(response_result), "tests/cxx/ctad.cc")
    assert response_record["invocation"] == changed["invocation"]

    assert "should add these lines:" not in first.stderr
    assert "The full include-list" not in first.stderr

    # Native serialization and identity preserve UTF-8 and quotes in paths;
    # command golden vectors separately cover literal backslashes.
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        escaped_source = Path(directory) / 'quote"grüße.cc'
        escaped_source.write_text("int escaped_path_fixture;\n")
        escaped_result = run(str(escaped_source), "--output_format=json")
        assert escaped_result.returncode == 0, escaped_result.stderr
        escaped = records(escaped_result)[0]
        translation_unit = str(escaped_source.resolve()).replace(os.sep, "/")
        direct_arguments = [str(IWYU), "-I", ".", "-std=c++17",
                            str(escaped_source)]
        expected_digest = command_digest(
            str(ROOT.resolve()).replace(os.sep, "/"), translation_unit,
            direct_arguments)
        assert escaped["invocation"]["translation_unit"] == translation_unit
        assert escaped["invocation"]["command_digest"] == expected_digest
        assert escaped["invocation"]["id"] == (
            "inv-000000-" + expected_digest[7:19])

    # Driver option parsing excludes regular-file operands of -include and -o
    # from translation-unit inference.
    operand_result = subprocess.run(
        [str(IWYU), "-Xiwyu", "--output_format=json", "-I", ".",
         "-std=c++17", "tests/native_json/clean.cc", "-include",
         "tests/native_json/unused.h", "-o", "tests/native_json/decoy.cc"],
        cwd=ROOT, text=True, capture_output=True)
    assert operand_result.returncode == 0, operand_result.stderr
    operand_record = records(operand_result)[0]
    assert operand_record["invocation"]["translation_unit"].endswith(
        "/tests/native_json/clean.cc")

    # Recursive response expansion is identity-equivalent to direct arguments,
    # including a final backslash byte in a nested response file.
    with tempfile.TemporaryDirectory() as response_directory:
        nested = Path(response_directory) / "nested.rsp"
        outer = Path(response_directory) / "outer.rsp"
        nested.write_bytes(b"-I . -std=c++17 -DTRAIL=tail\\")
        outer.write_text(f"@{nested} tests/native_json/clean.cc")
        nested_result = subprocess.run(
            [str(IWYU), "-Xiwyu", "--output_format=json", "@" + str(outer)],
            cwd=ROOT, text=True, capture_output=True)
        direct_result = subprocess.run(
            [str(IWYU), "-Xiwyu", "--output_format=json", "-I", ".",
             "-std=c++17", "-DTRAIL=tail\\", "tests/native_json/clean.cc"],
            cwd=ROOT, text=True, capture_output=True)
    assert nested_result.returncode == direct_result.returncode == 0
    assert records(nested_result)[0]["invocation"] == (
        records(direct_result)[0]["invocation"])

    # Clang's GNU response tokenizer drops explicit empty quoted arguments,
    # while clang-cl's Windows tokenizer retains them. Native execution and
    # direct identity must use the same effective vector in both modes.
    with tempfile.TemporaryDirectory() as response_directory:
        gnu_response = Path(response_directory) / "gnu-empty.rsp"
        gnu_response.write_text(
            '\"\" -I . -std=c++17 tests/native_json/clean.cc')
        gnu_result = subprocess.run(
            [str(IWYU), "-Xiwyu", "--output_format=json",
             "@" + str(gnu_response)],
            cwd=ROOT, text=True, capture_output=True)
    assert gnu_result.returncode == 0, gnu_result.stderr
    gnu_invocation = records(gnu_result)[0]["invocation"]
    gnu_arguments = [str(IWYU), "-I", ".", "-std=c++17",
                     "tests/native_json/clean.cc"]
    assert gnu_invocation["command_digest"] == command_digest(
        str(ROOT.resolve()).replace(os.sep, "/"),
        str((ROOT / "tests/native_json/clean.cc").resolve()).replace(
            os.sep, "/"),
        gnu_arguments)

    with tempfile.TemporaryDirectory() as response_directory:
        cl_response = Path(response_directory) / "cl-empty.rsp"
        cl_response.write_text(
            '\"\" -I . -std=c++17 /TP tests/native_json/clean.cc')
        cl_response_result = subprocess.run(
            [str(IWYU), "-Xiwyu", "--output_format=json",
             "--driver-mode=cl", "@" + str(cl_response)],
            cwd=ROOT, text=True, capture_output=True)
        cl_direct_result = subprocess.run(
            [str(IWYU), "-Xiwyu", "--output_format=json",
             "--driver-mode=cl", "", "-I", ".", "-std=c++17", "/TP",
             "tests/native_json/clean.cc"],
            cwd=ROOT, text=True, capture_output=True)
    assert cl_response_result.returncode == cl_direct_result.returncode == 0, (
        cl_response_result.stderr + cl_direct_result.stderr)
    cl_response_invocation = records(cl_response_result)[0]["invocation"]
    cl_direct_invocation = records(cl_direct_result)[0]["invocation"]
    assert cl_response_invocation == cl_direct_invocation
    cl_arguments = [str(IWYU), "--driver-mode=cl", "", "-I", ".",
                    "-std=c++17", "/TP", "tests/native_json/clean.cc"]
    assert cl_response_invocation["command_digest"] == command_digest(
        str(ROOT.resolve()).replace(os.sep, "/"),
        str((ROOT / "tests/native_json/clean.cc").resolve()).replace(
            os.sep, "/"),
        cl_arguments)

    # A successful no-change analysis still emits a complete record.
    clean_result = run("tests/native_json/clean.cc", "--output_format=json")
    assert clean_result.returncode == 0, clean_result.stderr
    clean = records(clean_result)[0]
    assert clean["status"] == "correct"
    assert clean["operations"] == []
    assert clean["desired_state"] == {
        "complete": True, "items": [], "status": "known"}

    configured_result = run(
        "tests/native_json/clean.cc", "--output_format=json",
        "--configuration=fastdebug")
    assert configured_result.returncode == 0, configured_result.stderr
    configured = records(configured_result)[0]
    assert configured["invocation"]["configuration"] == {
        "status": "known", "value": "fastdebug"}
    assert configured["analyzed_configurations"] == {
        "status": "known", "value": ["fastdebug"]}

    # Symbol-motivated additions expose known OneUse facts without inventing
    # origin, condition, or macro provenance.
    add = next(op for op in changed["operations"]
               if op["subject_action"] == "add")
    assert add["symbols"]
    use = add["symbols"][0]["uses"][0]
    assert use["requirement"] == "full"
    assert use["origin"] == "unknown"
    assert use["condition"]["status"] == "unavailable"
    assert use["macro"]["status"] == "unavailable"
    assert changed["diagnostics"]
    assert first.stderr == ""

    # Policy decisions survive final desired-state calculation.
    keep_result = run("tests/native_json/pragma_keep.cc",
                      "--output_format=json")
    keep = by_path(records(keep_result), "pragma_keep.cc")
    keep_op = keep["operations"][0]
    assert (keep_op["subject_action"], keep_op["disposition"],
            keep_op["reason_code"]) == ("remove", "keep", "pragma_keep")
    assert keep_op["spelling"] in {
        item["spelling"] for item in keep["desired_state"]["items"]}
    assert keep_op["spelling"] == (
        '#include "tests/native_json/unused.h"  // IWYU pragma: keep')

    needed_keep_result = run("tests/native_json/pragma_keep_needed.cc",
                             "--output_format=json")
    needed_keep = by_path(records(needed_keep_result),
                          "pragma_keep_needed.cc")
    assert not any(op["reason_code"] == "pragma_keep"
                   for op in needed_keep["operations"])

    export_result = run("tests/native_json/pragma_export.cc",
                        "--output_format=json")
    export_record = by_path(records(export_result), "pragma_export.cc")
    export_op = next(op for op in export_record["operations"]
                     if op["reason_code"] == "pragma_export")
    assert (export_op["subject_action"], export_op["disposition"]) == (
        "remove", "keep")

    inhibited_result = run("tests/native_json/no_include.cc",
                           "--output_format=json")
    inhibited = by_path(records(inhibited_result), "no_include.cc")
    inhibited_op = next(op for op in inhibited["operations"]
                        if op["reason_code"] == "pragma_no_include")
    assert (inhibited_op["subject_action"],
            inhibited_op["disposition"]) == ("add", "suppress")
    assert inhibited_op["symbols"]
    assert inhibited_op["spelling"] not in {
        item["spelling"] for item in inhibited["desired_state"]["items"]}

    association_suppression_result = run(
        "tests/native_json/associated.cc", "--output_format=json")
    association_source = by_path(
        records(association_suppression_result), "associated.cc")
    association_op = next(
        op for op in association_source["operations"]
        if op["reason_code"] == "provided_by_associated_header")
    assert (association_op["subject_action"],
            association_op["disposition"]) == ("add", "suppress")
    assert association_op["symbols"]

    prefix_result = subprocess.run(
        [str(IWYU), "-Xiwyu", "--output_format=json", "-Xiwyu",
         "--prefix_header_includes=remove", "-include",
         "tests/cxx/prefix_header_includes-d1.h", "-include",
         "tests/cxx/prefix_header_includes-d2.h", "-include",
         "tests/cxx/prefix_header_includes-d3.h", "-include",
         "tests/cxx/prefix_header_includes-d4.h", "-I", ".", "-std=c++17",
         "tests/cxx/prefix_header_includes_remove.cc"],
        cwd=ROOT, text=True, capture_output=True)
    assert prefix_result.returncode == 0, prefix_result.stderr
    prefix = by_path(records(prefix_result),
                     "prefix_header_includes_remove.cc")
    prefix_decisions = {
        (op["subject_action"], op["disposition"], op["reason_code"])
        for op in prefix["operations"]}
    assert ("remove", "apply", "prefix_header_remove") in prefix_decisions
    assert ("add", "suppress",
            "prefix_header_add_suppressed") in prefix_decisions
    assert all(op["disposition"] != "report" for op in prefix["operations"])

    # Applied include removals retain the exact physical source line while the
    # desired state remains canonical.
    exact_result = run("tests/native_json/exact_spelling.cc",
                       "--output_format=json")
    exact = by_path(records(exact_result), "exact_spelling.cc")
    assert exact["operations"][0]["spelling"] == (
        '  #  include   "tests/native_json/unused.h"  '
        '// exact removal spelling')
    assert exact["desired_state"]["items"] == []

    forward_result = run("tests/native_json/forward_declarations.cc",
                         "--output_format=json")
    assert forward_result.returncode == 0, forward_result.stderr
    forward = by_path(records(forward_result), "forward_declarations.cc")
    forward_ops = [
        op for op in forward["operations"]
        if op["item_kind"] == "forward_declaration"]

    def forward_op(reason_code, fragment):
        return next(op for op in forward_ops
                    if op["reason_code"] == reason_code and
                    fragment in op["spelling"])

    assert forward_op("undesired_item_present", "RemoveMe")["spelling"] == (
        "  class   RemoveMe  ;  // exact removable declaration")
    multiline_remove = forward_op("undesired_item_present", "MultiRemove")
    assert multiline_remove["spelling"] == (
        "  template <\n"
        "      typename T\n"
        "  >\n"
        "  class   MultiRemove  ;  // exact multiline removal")
    expected_keep_spellings = {
        ("pragma_keep", "KeepMe"):
            "  struct   KeepMe  ;  // IWYU pragma: keep",
        ("pragma_keep", "MultiKeep"): (
            "  template <\n"
            "      typename T\n"
            "  >\n"
            "  struct   MultiKeep  ;  // IWYU pragma: keep"),
        ("pragma_export", "ExportMe"):
            "  class   ExportMe  ;  // IWYU pragma: export",
        ("linkage_specification", "LinkageKept"):
            "  class   LinkageKept  ;",
        ("explicit_attribute", "AttributeKept"): (
            "struct __attribute__((aligned (16))) AttributeKept  ;"),
        ("nested_declaration", "NestedKept"):
            "  class   NestedKept  ;",
    }
    desired_forward_spellings = {
        item["spelling"] for item in forward["desired_state"]["items"]
        if item["item_kind"] == "forward_declaration"}
    for (reason_code, fragment), spelling in expected_keep_spellings.items():
        operation = forward_op(reason_code, fragment)
        assert (operation["subject_action"], operation["disposition"],
                operation["spelling"]) == ("remove", "keep", spelling)
        assert spelling in desired_forward_spellings
    assert not any(op["reason_code"] == "pragma_keep" and
                   "Needed" in op["spelling"]
                   for op in forward_ops)
    assert "  class   Needed  ;  // IWYU pragma: keep" in (
        desired_forward_spellings)

    generated_result = run("tests/native_json/generated_forward.cc",
                           "--output_format=json")
    assert generated_result.returncode == 0, generated_result.stderr
    generated = by_path(records(generated_result), "generated_forward.cc")
    generated_add = next(
        op for op in generated["operations"]
        if op["item_kind"] == "forward_declaration" and
        op["subject_action"] == "add")
    assert generated_add["spelling"] == "struct NativeJsonNeeded;"
    assert generated_add["spelling"] in {
        item["spelling"] for item in generated["desired_state"]["items"]}

    # Recoverable preprocessor, Clang frontend, and OneUse warnings become
    # diagnostics in JSON mode, not native stderr.
    warning_result = run("tests/native_json/preprocessor_warning.cc",
                         "--output_format=json")
    warning = by_path(records(warning_result), "preprocessor_warning.cc")
    assert warning_result.stderr == ""
    assert [diag["code"] for diag in warning["diagnostics"]] == [
        "clang_frontend_warning", "preprocessor_warning"]
    assert "native frontend warning fixture" in warning["diagnostics"][0][
        "message"]
    text_warning = run("tests/native_json/preprocessor_warning.cc")
    assert "native frontend warning fixture" in text_warning.stderr
    assert "Inhibited include must be a quoted header" in text_warning.stderr

    header_warning_result = run(
        "tests/native_json/frontend_warning_driver.cc",
        "--output_format=json",
        "--check_also=tests/native_json/frontend_warning.hpp")
    assert header_warning_result.stderr == ""
    header_warning_records = records(header_warning_result)
    warning_header = by_path(header_warning_records, "frontend_warning.hpp")
    warning_driver = by_path(header_warning_records,
                             "frontend_warning_driver.cc")
    assert [diag["code"] for diag in warning_header["diagnostics"]] == [
        "clang_frontend_warning"]
    assert "analyzed header frontend warning fixture" in (
        warning_header["diagnostics"][0]["message"])
    assert all(diag["code"] != "clang_frontend_warning"
               for diag in warning_driver["diagnostics"])

    unreported_header_warning = run(
        "tests/native_json/frontend_warning_driver.cc",
        "--output_format=json")
    assert unreported_header_warning.stderr == ""
    unreported_driver = by_path(
        records(unreported_header_warning), "frontend_warning_driver.cc")
    routed_warning = next(
        diag for diag in unreported_driver["diagnostics"]
        if diag["code"] == "clang_frontend_warning")
    assert routed_warning["location"]["value"]["path"].endswith(
        "frontend_warning.hpp")

    warning_note_result = run(
        "tests/native_json/frontend_warning_note.cc",
        "--output_format=json")
    assert warning_note_result.stderr == ""
    warning_note = by_path(
        records(warning_note_result), "frontend_warning_note.cc")
    assert [(diag["severity"], diag["code"])
            for diag in warning_note["diagnostics"]] == [
                ("warning", "clang_frontend_warning"),
                ("note", "clang_frontend_note")]

    frontend_error = run("tests/native_json/frontend_error.cc",
                         "--output_format=json")
    assert frontend_error.returncode != 0
    assert frontend_error.stdout == ""
    assert "error:" in frontend_error.stderr

    pragma_warning_result = run("tests/cxx/comment_pragmas.cc",
                                "--output_format=json")
    assert "No public header found" not in pragma_warning_result.stderr
    pragma_warning_record = by_path(
        records(pragma_warning_result), "comment_pragmas.cc")
    pragma_codes = [diag["code"]
                    for diag in pragma_warning_record["diagnostics"]]
    assert pragma_codes.count("preprocessor_warning") == 7
    assert "private_provider_without_public_mapping" in pragma_codes

    kinds_result = run("tests/native_json/kinds.cc", "--output_format=json",
                       "--check_also=tests/native_json/kinds*")
    kinds = {record["file"]["path"]: record["file"]["kind"]
             for record in records(kinds_result)}
    assert next(value for path, value in kinds.items()
                if path.endswith("kinds.cc")) == "source"
    assert next(value for path, value in kinds.items()
                if path.endswith("kinds.hpp")) == "header"
    assert next(value for path, value in kinds.items()
                if path.endswith("kinds.inline.hpp")) == "inline_header"
    assert next(value for path, value in kinds.items()
                if path.endswith("kinds.InLiNe.HpP")) == "inline_header"
    assert next(value for path, value in kinds.items()
                if path.endswith("kinds-InL.HH")) == "inline_header"
    other_result = subprocess.run(
        [str(IWYU), "-Xiwyu", "--output_format=json", "-x", "c++",
         "tests/native_json/kinds.unknown"],
        cwd=ROOT, text=True, capture_output=True)
    assert records(other_result)[0]["file"]["kind"] == "other"

    # Associations retain reason and relationship and records sort by path.
    associated_result = run("tests/cxx/associated_dot_inline.cc",
                            "--output_format=json")
    assert associated_result.returncode == 0, associated_result.stderr
    associated_records = records(associated_result)
    paths = [record["file"]["path"] for record in associated_records]
    assert paths == sorted(paths)
    source = by_path(associated_records, "associated_dot_inline.cc")
    association = source["associated_headers"][0]
    assert association["relationship"] == "inline_definitions"
    assert association["reason_code"] == "canonical_inline_stem"

    probe_result = run("tests/native_json/probe.cc", "--output_format=json")
    assert probe_result.returncode == 0, probe_result.stderr
    probe_records = records(probe_result)
    probe_header = by_path(probe_records, "probe-inl.hpp")

    # Verified dotted-inline consolidation exposes the actual pair-plus-sole
    # candidate union. Endpoint paths are anchored while include spelling is
    # retained for the includer.
    provider_result = run(
        "tests/native_json/assoc003_provider.cc", "--output_format=json")
    assert provider_result.returncode == 0, provider_result.stderr
    provider_record = by_path(records(provider_result),
                              "assoc003_provider.cc")
    provider_add = next(
        op for op in provider_record["operations"]
        if op["subject_action"] == "add" and op["item_kind"] == "include")
    pair_plain_path = str(
        (ROOT / "tests/cxx/assoc003_pair_provider.hpp").resolve()).replace(
            os.sep, "/")
    pair_inline_path = str(
        (ROOT / "tests/cxx/assoc003_pair_provider.inline.hpp").resolve()
    ).replace(os.sep, "/")
    pair_plain_ref = {
        "path": pair_plain_path,
        "spelling": '"tests/cxx/assoc003_pair_provider.hpp"',
    }
    pair_inline_ref = {
        "path": pair_inline_path,
        "spelling": '"tests/cxx/assoc003_pair_provider.inline.hpp"',
    }
    assert provider_add["provider"] == {
        "selected": {"status": "known", "value": pair_inline_ref},
        "candidates": {
            "status": "known",
            "value": [pair_plain_ref, pair_inline_ref],
        },
        "chain": {"status": "unavailable", "value": None},
    }
    provider_remove = next(
        op for op in provider_record["operations"]
        if op["subject_action"] == "remove")
    assert all(value == {"status": "not_applicable", "value": None}
               for value in provider_remove["provider"].values())

    relative_provider_result = run(
        "tests/native_json/association_a/../assoc003_provider.cc",
        "--output_format=json")
    assert relative_provider_result.returncode == 0, (
        relative_provider_result.stderr)
    relative_provider = by_path(
        records(relative_provider_result), "assoc003_provider.cc")
    relative_add = next(
        op for op in relative_provider["operations"]
        if op["subject_action"] == "add" and op["item_kind"] == "include")
    assert relative_add["provider"] == provider_add["provider"]
    assert probe_header["file"]["kind"] == "inline_header"

    sole_result = run(
        "tests/native_json/assoc003_provider_sole.cc", "--output_format=json")
    assert sole_result.returncode == 0, sole_result.stderr
    sole_record = by_path(records(sole_result), "assoc003_provider_sole.cc")
    sole_add = next(op for op in sole_record["operations"]
                    if op["subject_action"] == "add")
    assert sole_add["provider"]["selected"] == {
        "status": "known", "value": pair_inline_ref}
    assert sole_add["provider"]["candidates"] == {
        "status": "known", "value": [pair_inline_ref]}

    extra_result = run(
        "tests/native_json/assoc003_provider_extra.cc", "--output_format=json")
    assert extra_result.returncode == 0, extra_result.stderr
    extra_record = by_path(records(extra_result), "assoc003_provider_extra.cc")
    extra_add = next(op for op in extra_record["operations"]
                     if op["subject_action"] == "add")
    extra_ref = {
        "path": str((ROOT / "tests/native_json/assoc003_extra_provider.hpp")
                    .resolve()).replace(os.sep, "/"),
        "spelling": '"tests/native_json/assoc003_extra_provider.hpp"',
    }
    assert extra_add["provider"]["selected"] == {
        "status": "known", "value": pair_inline_ref}
    assert extra_add["provider"]["candidates"] == {
        "status": "known",
        "value": sorted([pair_plain_ref, pair_inline_ref, extra_ref],
                        key=lambda ref: (ref["path"], ref["spelling"])),
    }

    unresolved_result = run(
        "tests/native_json/assoc003_provider_unresolved.cc",
        "--output_format=json",
        "--mapping_file=tests/native_json/assoc003_unresolved.imp")
    assert unresolved_result.returncode == 0, unresolved_result.stderr
    unresolved_record = by_path(
        records(unresolved_result), "assoc003_provider_unresolved.cc")
    unresolved_add = next(op for op in unresolved_record["operations"]
                          if op["subject_action"] == "add")
    assert unresolved_add["provider"]["selected"] == {
        "status": "known", "value": pair_inline_ref}
    assert unresolved_add["provider"]["candidates"] == {
        "status": "unavailable", "value": None}

    disagreement_result = subprocess.run(
        [str(IWYU), "-Xiwyu", "--output_format=json", "-I", ".",
         "-I", "tests/native_json/assoc003_disagree_a",
         "-I", "tests/native_json/assoc003_disagree_b", "-std=c++17",
         "tests/native_json/assoc003_provider_disagreement.cc"],
        cwd=ROOT, text=True, capture_output=True)
    assert disagreement_result.returncode == 0, disagreement_result.stderr
    disagreement_record = by_path(
        records(disagreement_result), "assoc003_provider_disagreement.cc")
    disagreement_add = next(op for op in disagreement_record["operations"]
                            if op["subject_action"] == "add")
    assert disagreement_add["provider"]["selected"] == {
        "status": "unavailable", "value": None}
    assert disagreement_add["provider"]["candidates"] == {
        "status": "unavailable", "value": None}

    pch_result = run("tests/native_json/assoc003_pch_pair.cc",
                     "--output_format=json", "--pch_in_code")
    assert pch_result.returncode == 0, pch_result.stderr
    pch_record = by_path(records(pch_result), "assoc003_pch_pair.cc")
    pch_desired = {item["spelling"]
                   for item in pch_record["desired_state"]["items"]}
    assert '#include "tests/native_json/assoc003_pch_pair.hpp"' in pch_desired
    assert ('#include "tests/native_json/assoc003_pch_pair.inline.hpp"'
            in pch_desired)
    assert not any(op["spelling"] ==
                   '#include "tests/native_json/assoc003_pch_pair.hpp"'
                   for op in pch_record["operations"])

    mapped_negative_result = run(
        "tests/native_json/assoc003_mapped_negative.cc",
        "--output_format=json",
        "--mapping_file=tests/native_json/assoc003_mapped_negative.imp")
    assert mapped_negative_result.returncode == 0, (
        mapped_negative_result.stderr)
    mapped_negative = by_path(
        records(mapped_negative_result), "assoc003_mapped_negative.cc")
    mapped_desired = {item["spelling"]
                      for item in mapped_negative["desired_state"]["items"]}
    assert ('#include "tests/native_json/assoc003_mapped_negative.hpp"'
            in mapped_desired)
    probe_source = by_path(probe_records, "probe.cc")
    assert any(
        association["path"].endswith("probe-inl.hpp") and
        association["relationship"] == "inline_definitions" and
        association["reason_code"] == "canonical_inline_stem"
        for association in probe_source["associated_headers"])

    pragma_result = run("tests/cxx/pragma_associated.cc",
                        "--output_format=json")
    assert pragma_result.returncode == 0, pragma_result.stderr
    pragma_source = by_path(records(pragma_result), "pragma_associated.cc")
    association_pairs = {
        (association["relationship"], association["reason_code"])
        for association in pragma_source["associated_headers"]}
    assert ("explicit", "pragma_associated") in association_pairs
    assert ("declarations", "canonical_stem") in association_pairs

    heuristic_result = run(
        "tests/native_json/association_a/heuristic.cc",
        "--output_format=json")
    heuristic_source = by_path(records(heuristic_result), "heuristic.cc")
    heuristic_association = heuristic_source["associated_headers"][0]
    assert (heuristic_association["relationship"],
            heuristic_association["reason_code"]) == (
                "heuristic", "first_include_basename")

    pch_result = subprocess.run(
        [str(IWYU), "-Xiwyu", "--output_format=json", "-Xiwyu",
         "--pch_in_code", "-Xiwyu", "--prefix_header_includes=remove",
         "-I", ".", "-std=c++17", "tests/cxx/pch_in_code.cc"],
        cwd=ROOT, text=True, capture_output=True)
    assert pch_result.returncode == 0, pch_result.stderr
    pch_source = by_path(records(pch_result), "pch_in_code.cc")
    assert any(
        association["path"].endswith("public/pch_in_code.h") and
        association["relationship"] == "heuristic" and
        association["reason_code"] == "pch_following_include_basename"
        for association in pch_source["associated_headers"])
    assert any(op["reason_code"] == "pch_in_code" and
               op["disposition"] == "keep"
               for op in pch_source["operations"])

    source_include_result = run("tests/native_json/source_include.cc",
                                "--output_format=json")
    source_include = by_path(records(source_include_result),
                             "source_include.cc")
    assert any(op["reason_code"] == "source_include" and
               op["disposition"] == "keep"
               for op in source_include["operations"])

    reexport_result = run(
        "tests/native_json/reexport_driver.cc", "--output_format=json",
        "--check_also=tests/native_json/reexport.inline.hpp")
    reexport = by_path(records(reexport_result), "reexport.inline.hpp")
    assert any(op["reason_code"] == "reexported_include" and
               op["disposition"] == "keep"
               for op in reexport["operations"])

    # Structured reason retention must not turn synthetic export protection into
    # FileEntry-wide keep state that defeats duplicate cleanup in text mode.
    with tempfile.TemporaryDirectory() as duplicate_directory:
        duplicate_root = Path(duplicate_directory)
        (duplicate_root / "x.h").write_text(
            "#pragma once\nstruct DuplicateExportX {};\n")
        duplicate_source = duplicate_root / "main.cc"
        duplicate_source.write_text(
            '#include "x.h"  // IWYU pragma: export\n#include "x.h"\n')
        duplicate_text = subprocess.run(
            [str(IWYU), "-I", str(duplicate_root), str(duplicate_source)],
            cwd=ROOT, text=True, capture_output=True)
        assert duplicate_text.returncode == 0
        assert "should remove these lines:" in duplicate_text.stderr
        assert "lines 2-2" in duplicate_text.stderr

    # Duplicate occurrence edits are explicitly skipped in schema v1.
    duplicate_result = run("tests/cxx/double_include.cc",
                           "--output_format=json")
    assert duplicate_result.returncode == 0, duplicate_result.stderr
    duplicate = by_path(records(duplicate_result), "double_include.cc")
    assert duplicate["status"] == "skipped"
    assert duplicate["operations"] == []
    assert duplicate["desired_state"]["status"] == "unavailable"
    assert [diagnostic["code"] for diagnostic in duplicate["diagnostics"]] == [
        "duplicate_occurrence_unrepresentable"]

    # Driver-owned metadata is accepted, validated, and reflected exactly.
    digest = "sha256:" + "a" * 64
    translation_unit = str((ROOT / "tests/native_json/clean.cc").resolve())
    metadata_result = run(
        "tests/native_json/clean.cc", "--output_format=json",
        "--invocation_index=7", "--command_digest=" + digest,
        "--translation_unit=" + translation_unit)
    metadata = records(metadata_result)[0]["invocation"]
    assert metadata == {
        "command_digest": digest,
        "configuration": {"status": "unavailable", "value": None},
        "id": "inv-000007-aaaaaaaaaaaa",
        "index": 7,
        "translation_unit": translation_unit,
    }

    # Intercepted informational output never contaminates structured stdout,
    # even when the JSON option appears after the intercepted option.
    for intercepted in ("--help", "--version"):
        for command in (
                [str(IWYU), "-Xiwyu", "--output_format=json", intercepted],
                [str(IWYU), intercepted, "-Xiwyu", "--output_format=json"]):
            info = subprocess.run(command, cwd=ROOT, text=True,
                                  capture_output=True)
            assert info.returncode == 0
            assert info.stdout == ""
            assert info.stderr

    for abbreviated in ("--output_f=json", "--o=json"):
        for intercepted in ("--help", "--version"):
            info = subprocess.run(
                [str(IWYU), intercepted, "-Xiwyu", abbreviated],
                cwd=ROOT, text=True, capture_output=True)
            assert info.returncode == 0
            assert info.stdout == ""
            assert info.stderr

    invalid = run(
        "tests/native_json/clean.cc", "--output_format=json",
        "--invocation_index=7", "--invocation_id=inv-000007-bbbbbbbbbbbb",
        "--command_digest=" + digest,
        "--translation_unit=" + translation_unit)
    assert invalid.returncode != 0
    assert invalid.stdout == ""
    assert "does not match" in invalid.stderr


if __name__ == "__main__":
    main()
