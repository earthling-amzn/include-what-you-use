#!/usr/bin/env python3
"""Opaque generic SEM-004 definition-availability contracts."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "inline_definition_availability"
IWYU = Path(sys.argv[1]).resolve()
CXX = Path(sys.argv[2]).resolve()
DRIVER = ROOT / "iwyu_tool.py"


def run(command, cwd=CORPUS, stdin=None, expect=0):
    result = subprocess.run([str(arg) for arg in command], cwd=cwd,
                            input=stdin, text=True, capture_output=True)
    assert result.returncode == expect, (
        f"command returned {result.returncode}, expected {expect}: {command}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def analyze(filename, *options, corpus=CORPUS, schema=1):
    command = [IWYU, "-Xiwyu", "--output_format=json"]
    if schema != 1:
        command += ["-Xiwyu", f"--output_schema={schema}"]
    command += ["-I", ".", "-std=c++17", *options, filename]
    first = run(command, cwd=corpus)
    second = run(command, cwd=corpus)
    assert second.stdout == first.stdout, filename
    records = [json.loads(line) for line in first.stdout.splitlines()]
    return next(record for record in records
                if record["file"]["path"] == filename), records


def definition_diagnostics(record):
    return [fact for fact in record["diagnostics"]
            if fact["code"] in {"missing_inline_definition",
                                "missing_template_definition",
                                "definition_provider_unproven"}]


def analyze_with_driver(entries, corpus=CORPUS):
    """Run real schema-v2 driver records for (filename, IWYU options)."""
    with tempfile.TemporaryDirectory(prefix="sem004-driver-",
                                     dir=ROOT / "build") as temporary:
        database = Path(temporary)
        commands = []
        for filename, options in entries:
            source = (corpus / filename).resolve()
            commands.append({
                "directory": str(corpus.resolve()),
                "file": str(source),
                "arguments": [str(CXX), "-I", ".", "-std=c++17",
                              *options, "-c", str(source)],
            })
        (database / "compile_commands.json").write_text(json.dumps(commands))
        environment = os.environ.copy()
        environment["IWYU_BINARY"] = str(IWYU)
        result = subprocess.run(
            [sys.executable, str(DRIVER), "-o", "json",
             "--output-schema", "2", "-j", "2", "-p", str(database)],
            cwd=ROOT, env=environment, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert result.stderr == "", result.stderr
        records = [json.loads(line) for line in result.stdout.splitlines()]
        assert records
        assert all(record["schema_version"] == 2 for record in records)
        return records


def semantic_keeps(record):
    return [op for op in record["operations"]
            if op["reason_code"] == "semantic_definition_provider"]


def assert_provider_keep(operation, spelling, chain):
    assert (operation["subject_action"], operation["disposition"],
            operation["spelling"]) == ("keep", "keep", spelling)
    assert operation["source_range"]["status"] == "known"
    selected = operation["provider"]["selected"]
    assert selected["status"] == "known"
    include_token = spelling.split()[1]
    assert selected["value"]["spelling"] == include_token
    assert operation["provider"]["chain"] == {
        "status": "known", "value": chain}
    assert chain[0] == operation["source_range"]["value"]["start"]["path"]
    assert chain[-1] == selected["value"]["path"]
    assert operation["symbols"]
    identities = [(symbol["name"], symbol["qualified_name"]["value"])
                  for symbol in operation["symbols"]]
    assert len(identities) == len(set(identities)), identities
    for symbol in operation["symbols"]:
        assert symbol["qualified_name"]["status"] == "known"
        assert symbol["declaration"]["status"] == "known"
        assert symbol["uses"]
        assert all(use["origin"] == "ast" and
                   use["requirement"] == "full" and
                   use["location"]["path"] == chain[0]
                   for use in symbol["uses"]), symbol


def assert_analyzer_contracts():
    available, _ = analyze("available.cc")
    assert available["status"] == "correct"
    assert available["diagnostics"] == []
    assert_provider_keep(semantic_keeps(available)[0],
                         '#include "api.inline.hpp"',
                         ["available.cc", "api.inline.hpp"])
    assert available["desired_state"]["items"] == [{
        "item_kind": "include",
        "spelling": '#include "api.inline.hpp"',
        "symbols": [
            "InlineAddress", "InlineFree", "InlineLifecycle",
            "InlineLifecycle::AddressMember",
            "InlineLifecycle::InlineLifecycle", "InlineLifecycle::Member",
            "InlineLifecycle::~InlineLifecycle", "InlineTemplate",
        ],
    }]

    missing, _ = analyze("missing.cc")
    assert not [op for op in missing["operations"]
                if op["subject_action"] == "add"]
    facts = [diagnostic for diagnostic in missing["diagnostics"]
             if diagnostic["code"] == "missing_inline_definition"]
    assert [fact["location"]["value"]["line"] for fact in facts] == [
        4, 4, 5, 6, 7, 7, 8]
    assert all("inline-definition provider" in fact["message"]
               for fact in facts)

    contexts, _ = analyze("contexts.cc")
    assert definition_diagnostics(contexts) == []
    pure_pointer, _ = analyze("pure_virtual_pointer.cc")
    assert definition_diagnostics(pure_pointer) == []
    pure_qualified, _ = analyze("pure_virtual_qualified.cc")
    assert [fact["code"] for fact in definition_diagnostics(pure_qualified)] == [
        "missing_inline_definition"]
    control, _ = analyze("control.cc")
    assert definition_diagnostics(control) == []
    assert [(op["subject_action"], op["disposition"], op["spelling"])
            for op in control["operations"]] == [
        ("remove", "apply", '#include "unused.inline.hpp"')]

    template_available, _ = analyze("template_available.cc")
    assert definition_diagnostics(template_available) == []
    keep = semantic_keeps(template_available)
    assert len(keep) == 1
    assert_provider_keep(keep[0], '#include "template_bodies.hpp"', [
        "template_available.cc", "template_bodies.hpp"])
    qualified = {symbol["qualified_name"]["value"]
                 for symbol in keep[0]["symbols"]}

    duplicate_calls, _ = analyze("duplicate_calls.cc")
    duplicate_keep = semantic_keeps(duplicate_calls)
    assert len(duplicate_keep) == 1
    assert_provider_keep(duplicate_keep[0], '#include "template_bodies.hpp"',
                         ["duplicate_calls.cc", "template_bodies.hpp"])
    assert len(duplicate_keep[0]["symbols"]) == 1
    assert len(duplicate_keep[0]["symbols"][0]["uses"]) == 2
    assert {"TemplateFunction", "TemplateBox<int>::Member",
            "TemplateBox<int>::AddressMember",
            "TemplateBox<int>::MemberTemplate"} <= qualified

    template_missing, _ = analyze("template_missing.cc")
    missing_templates = [fact for fact in definition_diagnostics(template_missing)
                         if fact["code"] == "missing_template_definition"]
    names = {fact["message"].split("'")[1] for fact in missing_templates}
    assert {"TemplateFunction", "TemplateBox<int>::TemplateBox",
            "TemplateBox<int>::~TemplateBox", "TemplateBox<int>::Member",
            "TemplateBox<int>::AddressMember",
            "TemplateBox<int>::MemberTemplate"} <= names
    assert not [op for op in template_missing["operations"]
                if op["subject_action"] == "add"]

    template_contexts, _ = analyze("template_contexts.cc")
    assert definition_diagnostics(template_contexts) == []

    extern_consumer, _ = analyze("extern_consumer.cc")
    assert definition_diagnostics(extern_consumer) == []
    assert semantic_keeps(extern_consumer) == []
    late_extern, _ = analyze("late_extern.cc")
    assert [fact["code"] for fact in definition_diagnostics(late_extern)] == [
        "missing_template_definition"]
    assert definition_diagnostics(late_extern)[0]["location"]["value"]["line"] == 3
    for feature in (0, 1):
        conditional_extern, _ = analyze(
            "conditional_extern.cc", f"-DFEATURE={feature}")
        assert [fact["code"] for fact in
                definition_diagnostics(conditional_extern)] == [
            "missing_template_definition"]
    instantiations, _ = analyze("extern_instantiations.cc")
    assert definition_diagnostics(instantiations) == []
    instantiation_keep = semantic_keeps(instantiations)[0]
    assert_provider_keep(instantiation_keep,
                         '#include "template_bodies.hpp"', [
                             "extern_instantiations.cc",
                             "template_bodies.hpp"])
    instantiation_names = {symbol["qualified_name"]["value"]
                           for symbol in instantiation_keep["symbols"]}
    assert "TemplateFunction" in instantiation_names
    assert "TemplateBox<int>::TemplateBox" in instantiation_names
    assert all(use["location"]["line"] <= 4
               for symbol in instantiation_keep["symbols"]
               for use in symbol["uses"])

    direct_after, _ = analyze("direct_after.cc")
    assert definition_diagnostics(direct_after) == []
    body_keep = next(op for op in semantic_keeps(direct_after)
                     if op["spelling"] == '#include "api.inline.hpp"')
    assert_provider_keep(body_keep,
                         '#include "api.inline.hpp"',
                         ["direct_after.cc", "api.inline.hpp"])
    assert body_keep["source_range"]["value"]["start"]["line"] == 5

    dominating, _ = analyze("conditional_dominating.cc", "-DFEATURE=1")
    assert definition_diagnostics(dominating) == []
    dominating_body = next(
        op for op in semantic_keeps(dominating)
        if op["spelling"] == '#include "api.inline.hpp"')
    assert dominating_body["edit_condition"]["status"] == "known"
    inactive, _ = analyze("conditional_dominating.cc", "-DFEATURE=0")
    assert definition_diagnostics(inactive) == []

    nondominating, _ = analyze("conditional_nondominating.cc", "-DFEATURE=1")
    assert [fact["code"] for fact in definition_diagnostics(nondominating)] == [
        "definition_provider_unproven"]
    assert semantic_keeps(nondominating) == []
    nondominating_inactive, _ = analyze(
        "conditional_nondominating.cc", "-DFEATURE=0")
    assert [fact["code"] for fact in
            definition_diagnostics(nondominating_inactive)] == [
        "missing_inline_definition"]

    exported, _ = analyze("export_consumer.cc")
    assert definition_diagnostics(exported) == []
    assert_provider_keep(semantic_keeps(exported)[0],
                         '#include "export_carrier.hpp"', [
                             "export_consumer.cc", "export_carrier.hpp"])

    mapped, _ = analyze("mapped_consumer.cc", "-Xiwyu",
                        "--mapping_file=providers.imp")
    assert definition_diagnostics(mapped) == []
    assert_provider_keep(semantic_keeps(mapped)[0],
                         '#include "mapped_carrier.hpp"', [
                             "mapped_consumer.cc", "mapped_carrier.hpp"])

    repeated_zero, _ = analyze("repeated_provider.cc", "-DFEATURE=0")
    assert definition_diagnostics(repeated_zero) == []
    assert_provider_keep(semantic_keeps(repeated_zero)[0],
                         '#include "repeat_second.hpp"', [
                             "repeated_provider.cc", "repeat_second.hpp"])
    repeated_one, _ = analyze("repeated_provider.cc", "-DFEATURE=1")
    assert [fact["code"] for fact in definition_diagnostics(repeated_one)] == [
        "definition_provider_unproven"]
    assert semantic_keeps(repeated_one) == []

    associated, associated_records = analyze("associated_consumer.cc")
    assert definition_diagnostics(associated) == []
    assert_provider_keep(semantic_keeps(associated)[0],
                         '#include "associated_owner.hpp"  // IWYU pragma: associated', [
                             "associated_consumer.cc", "associated_owner.hpp"])
    assert [(op["subject_action"], op["disposition"], op["reason_code"])
            for op in associated["operations"]
            if op["spelling"] == '#include "template_bodies.hpp"'] == [
        ("add", "suppress", "provided_by_associated_header")]
    owner = next(record for record in associated_records
                 if record["file"]["path"] == "associated_owner.hpp")
    assert semantic_keeps(owner) == []
    assert all(use["location"]["path"] == owner["file"]["path"]
               for operation in owner["operations"]
               for symbol in operation["symbols"]
               for use in symbol["uses"])

    # Both published schemas carry the same generic provider semantics.
    schema_two, _ = analyze("template_available.cc", schema=2)
    schema_two_keep = semantic_keeps(schema_two)[0]
    assert schema_two["schema_version"] == 2
    assert all(symbol["declaration_kind"] == {
        "status": "unavailable", "value": None}
        for symbol in schema_two_keep["symbols"])


def assert_link_fails(object_file, executable, symbols, cwd=CORPUS):
    link = subprocess.run([str(CXX), str(object_file), "-o", str(executable)],
                          cwd=cwd, text=True, capture_output=True)
    assert link.returncode != 0, link.stdout + link.stderr
    output = link.stdout + link.stderr
    for symbol in symbols:
        assert symbol in output, output


def assert_driver_contracts():
    entries = [
        ("available.cc", []),
        ("duplicate_calls.cc", []),
        ("duplicate_calls.cc", []),
        ("export_consumer.cc", []),
        ("mapped_consumer.cc",
         ["-Xiwyu", "--mapping_file=providers.imp"]),
        ("associated_consumer.cc", []),
    ]
    records = analyze_with_driver(entries)

    source_records = {}
    for record in records:
        for operation in record["operations"]:
            for symbol in operation["symbols"]:
                assert all(use["location"]["path"] == record["file"]["path"]
                           for use in symbol["uses"]), (record, operation)
        index = record["invocation"]["index"]
        if record["file"]["path"] == record["invocation"]["translation_unit"]:
            source_records[index] = record
    assert set(source_records) == set(range(len(entries)))

    expected = {
        0: ("available.cc", '#include "api.inline.hpp"'),
        3: ("export_consumer.cc", '#include "export_carrier.hpp"'),
        4: ("mapped_consumer.cc", '#include "mapped_carrier.hpp"'),
        5: ("associated_consumer.cc",
            '#include "associated_owner.hpp"  // IWYU pragma: associated'),
    }
    for index, (filename, spelling) in expected.items():
        keep = semantic_keeps(source_records[index])
        assert len(keep) == 1, (filename, keep)
        selected_path = keep[0]["provider"]["selected"]["value"]["path"]
        assert_provider_keep(
            keep[0], spelling,
            [source_records[index]["file"]["path"], selected_path])

    # Two source calls become two sorted use facts on one symbol object, and
    # duplicate compilation commands remain distinct driver invocations.
    duplicate_ids = []
    for index in (1, 2):
        record = source_records[index]
        duplicate_ids.append(record["invocation"]["id"])
        keep = semantic_keeps(record)
        assert len(keep) == 1
        symbols = keep[0]["symbols"]
        assert len(symbols) == 1, symbols
        assert symbols[0]["qualified_name"]["value"] == "TemplateFunction"
        assert len(symbols[0]["uses"]) == 2, symbols[0]
        assert symbols[0]["uses"] == sorted(
            symbols[0]["uses"],
            key=lambda use: (use["location"]["path"],
                             use["location"]["line"],
                             use["location"]["column"]))
    assert len(set(duplicate_ids)) == 2

    owner = next(record for record in records
                 if record["invocation"]["index"] == 5 and
                 record["file"]["path"].endswith("associated_owner.hpp"))
    assert semantic_keeps(owner) == []


def assert_gcc_backstop(corpus=CORPUS):
    with tempfile.TemporaryDirectory(prefix="sem004-gcc-", dir=ROOT / "build") as tmp:
        out = Path(tmp)
        for source in ("available.cc", "contexts.cc", "pure_virtual_pointer.cc",
                       "template_available.cc", "duplicate_calls.cc",
                       "template_contexts.cc",
                       "direct_after.cc", "export_consumer.cc",
                       "mapped_consumer.cc", "associated_consumer.cc"):
            executable = out / source.removesuffix(".cc")
            run([CXX, "-std=c++17", "-fno-inline", "-I", ".", source,
                 "-o", executable], cwd=corpus)
            run([executable], cwd=corpus)

        control = out / "control"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "control.cc",
             "noninline.cc", "-o", control], cwd=corpus)
        run([control], cwd=corpus)

        missing_object = out / "missing.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "missing.cc", "-o", missing_object], cwd=corpus)
        assert_link_fails(missing_object, out / "missing", [
            "InlineLifecycle::InlineLifecycle()", "InlineAddress(int)",
            "InlineFree(int)", "int InlineTemplate<int>(int)"], cwd=corpus)

        template_missing_object = out / "template_missing.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "template_missing.cc", "-o", template_missing_object], cwd=corpus)
        assert_link_fails(template_missing_object, out / "template_missing", [
            "int TemplateFunction<int>(int)",
            "TemplateBox<int>::TemplateBox()",
            "TemplateBox<int>::Member() const"], cwd=corpus)

        late_extern_object = out / "late_extern.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "late_extern.cc", "-o", late_extern_object], cwd=corpus)
        assert_link_fails(late_extern_object, out / "late_extern", [
            "int TemplateFunction<int>(int)"], cwd=corpus)

        instantiations = out / "extern_instantiations.o"
        consumer = out / "extern_consumer.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "extern_instantiations.cc", "-o", instantiations], cwd=corpus)
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "extern_consumer.cc", "-o", consumer], cwd=corpus)
        extern_program = out / "extern_consumer"
        run([CXX, consumer, instantiations, "-o", extern_program], cwd=corpus)
        run([extern_program], cwd=corpus)

        for feature in (0, 1):
            for source in ("conditional_dominating.cc", "repeated_provider.cc"):
                executable = out / f"{source.removesuffix('.cc')}-{feature}"
                run([CXX, "-std=c++17", "-fno-inline", "-I", ".",
                     f"-DFEATURE={feature}", source, "-o", executable], cwd=corpus)
                run([executable], cwd=corpus)
        nondominating = out / "conditional_nondominating-1"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-DFEATURE=1",
             "conditional_nondominating.cc", "-o", nondominating], cwd=corpus)
        run([nondominating], cwd=corpus)
        nondominating_object = out / "conditional_nondominating-0.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-DFEATURE=0",
             "-c", "conditional_nondominating.cc", "-o",
             nondominating_object], cwd=corpus)
        assert_link_fails(nondominating_object,
                          out / "conditional_nondominating-0",
                          ["InlineFree(int)"], cwd=corpus)

        pure_qualified_object = out / "pure_virtual_qualified.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "pure_virtual_qualified.cc", "-o", pure_qualified_object],
            cwd=corpus)
        assert_link_fails(pure_qualified_object, out / "pure_virtual_qualified",
                          ["PureBase::MissingBody()"], cwd=corpus)


def assert_fixer_idempotence():
    with tempfile.TemporaryDirectory(prefix="sem004-fix-", dir=ROOT / "build") as tmp:
        corpus = Path(tmp) / "inline_definition_availability"
        shutil.copytree(CORPUS, corpus)
        cases = {
            "available.cc": [], "contexts.cc": [], "control.cc": [],
            "pure_virtual_pointer.cc": [], "template_available.cc": [],
            "duplicate_calls.cc": [], "template_contexts.cc": [],
            "export_consumer.cc": [], "mapped_consumer.cc": [
                "-Xiwyu", "--mapping_file=providers.imp"],
            "conditional_dominating.cc": ["-DFEATURE=1"],
            "repeated_provider.cc": ["-DFEATURE=0"],
            "extern_consumer.cc": [], "extern_instantiations.cc": [],
        }
        for filename, options in cases.items():
            command = [IWYU, "-I", ".", "-std=c++17", *options, filename]
            first = run(command, cwd=corpus)
            run([sys.executable, ROOT / "fix_includes.py", "--nosafe_headers",
                 "--nocomments", "--noblank_lines"], cwd=corpus,
                stdin=first.stderr)
            second = run(command, cwd=corpus)
            assert "should add these lines:" not in second.stderr, second.stderr
            assert "should remove these lines:" not in second.stderr, second.stderr
        assert '#include "api.inline.hpp"' in (corpus / "available.cc").read_text()
        assert "unused.inline.hpp" not in (corpus / "control.cc").read_text()
        assert_gcc_backstop(corpus)


if __name__ == "__main__":
    assert_analyzer_contracts()
    assert_driver_contracts()
    assert_gcc_backstop()
    assert_fixer_idempotence()
    print("generic SEM-004 definition-availability contracts passed")
