#!/usr/bin/env python3
"""Opaque MC-002 conditional-region census contract."""

import copy
import importlib.util
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
IWYU = Path(sys.argv[1]).resolve()
CXX = sys.argv[2]
SCHEMA = json.loads((ROOT / "docs/output-v2.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
SOURCE = "tests/conditional_regions/conditional.cc"
SCOPE_SOURCE = "tests/conditional_regions/scope.cc"
NON_FILE_SOURCE = "tests/conditional_regions/non_file.cc"
NON_FILE_KEEP_SOURCES = {
    scope: f"tests/conditional_regions/non_file_keep_{scope}.cc"
    for scope in ("function", "class", "namespace", "declaration")
}
SPLICED_SOURCE = "tests/conditional_regions/spliced.cc"
NESTED_SOURCE = "tests/conditional_regions/nested.cc"
GUARDED_SOURCE = "tests/conditional_regions/guarded.cc"
GUARDED_HEADER = "tests/conditional_regions/guarded.hpp"
DRIVER = ROOT / "iwyu_tool.py"

CONFIGS = {
    "server": ["-DCOMPILER2", "-DARCH_TEST", "-DX86"],
    "zero": ["-DZERO"],
    "aarch64": ["-DARCH_TEST"],
    "generic": [],
}
EXPECTED = {
    "server": ["active", "inactive", "inactive", "active", "active", "inactive"],
    "zero": ["inactive", "active", "inactive", "inactive", "inactive", "inactive"],
    "aarch64": ["inactive", "inactive", "active", "active", "inactive", "active"],
    "generic": ["inactive", "inactive", "active", "inactive", "inactive", "inactive"],
}
EXPECTED_ADDS = {
    "server": {"jit.hpp": "branch-47-0-47", "x86.hpp": "branch-333-0-333"},
    "zero": {"zero.hpp": "branch-47-1-137"},
    "aarch64": {"generic.hpp": "branch-47-2-225",
                  "aarch64.hpp": "branch-333-1-421"},
    "generic": {"generic.hpp": "branch-47-2-225"},
}


def analyze(configuration, flags, source=SOURCE, target=None, iwyu_args=()):
    command = [str(IWYU), "-Xiwyu", "--output_format=json", "-Xiwyu",
               "--output_schema=2", "-Xiwyu",
               "--configuration=" + configuration]
    for argument in iwyu_args:
        command.extend(["-Xiwyu", argument])
    command.extend(["-I", ".", "-std=c++17", *flags, source])
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    for record in records:
        VALIDATOR.validate(record)
    selected = target or source
    return next(record for record in records
                if record["file"]["path"].endswith(selected.rsplit("/", 1)[-1]))


def assert_non_file_keep(record, source):
    assert record["status"] == "correct"
    assert record["diagnostics"] == []
    assert len(record["conditional_regions"]) == 1
    region = record["conditional_regions"][0]
    assert (region["activity"], region["lexical_scope"]) == \
        ("active", "non_file")

    assert len(record["operations"]) == 1
    operation = record["operations"][0]
    assert (operation["subject_action"], operation["item_kind"],
            operation["disposition"], operation["reason_code"]) == \
        ("remove", "include", "keep", "pragma_keep")
    assert operation["spelling"] == \
        '#include "tests/conditional_regions/empty.hpp"  // IWYU pragma: keep'
    condition = operation["edit_condition"]
    assert condition["status"] == "known"
    assert condition["value"]["active_branch"] is True
    assert condition["value"]["source_region_id"] == \
        region["source_region_id"]
    assert condition["value"]["source_region_path"] == \
        [region["source_region_id"]]
    assert condition["value"]["location"]["value"]["path"].endswith(source)
    assert operation["multi_config"]["value"] == [{
        "condition": condition,
        "configuration": "zero",
        "disposition": "keep",
        "subject_action": "remove",
    }]
    assert operation["spelling"] in {
        item["spelling"] for item in record["desired_state"]["items"]}
    return operation


def main():
    source_bytes = (ROOT / SOURCE).read_bytes()
    records = {name: analyze(name, flags) for name, flags in CONFIGS.items()}
    identities = None
    for name, record in records.items():
        regions = record["conditional_regions"]
        assert record["declaration_facts"]
        assert record["declaration_conflicts"] == []
        assert record["source_identity"]
        assert record["source_digest"]
        assert record["file_digest"] == {
            "status": "known",
            "value": "sha256:" + hashlib.sha256(source_bytes).hexdigest(),
        }
        current = [region["source_region_id"] for region in regions]
        if identities is None:
            identities = current
        assert current == identities
        assert [region["activity"] for region in regions] == EXPECTED[name]
        assert all(region["lexical_scope"] in ("file", "unknown")
                   for region in regions)
        assert all(region["lexical_scope"] == "file"
                   for region in regions if region["activity"] == "active")
        assert len(regions) == 6
        assert all(region["body_end_offset"]["status"] == "known"
                   for region in regions)
        for region in regions:
            offset = region["directive_offset"]
            keyword = region["directive_kind"].encode()
            assert source_bytes[offset:offset + len(keyword)] == keyword
            assert region["directive_location"]["value"]["path"] == SOURCE
        adds = {}
        for operation in record["operations"]:
            if operation["subject_action"] != "add":
                continue
            basename = operation["provider"]["selected"]["value"]["path"].rsplit("/", 1)[-1]
            condition = operation["edit_condition"]["value"]
            adds[basename] = condition["source_region_id"]
            assert condition["source_region_path"][-1] == condition["source_region_id"]
        assert adds == EXPECTED_ADDS[name], (name, adds)

        compile_result = subprocess.run(
            [CXX, "-std=c++17", "-I", str(ROOT), *CONFIGS[name],
             "-fsyntax-only", str(ROOT / SOURCE)],
            text=True, capture_output=True)
        assert compile_result.returncode == 0, compile_result.stderr

    # The architecture leaf paths prove nesting by exact source IDs, not by
    # equivalent expression strings.
    arch = next(operation for operation in records["server"]["operations"]
                if operation["subject_action"] == "add" and
                operation["spelling"].endswith('x86.hpp"'))
    assert arch["edit_condition"]["value"]["source_region_path"] == [
        "branch-310-0-310", "branch-333-0-333"]

    # Expanded braces reject ordinary and macro-created scopes. The completed
    # AST additionally rejects brace-depth-zero directives embedded in the
    # exact alias repro and in parameter, template, linkage, and initializer
    # ranges. Only the normal top-of-file branch is include-capable.
    scope_flags = ["-DFILE_REGION", "-DFUNCTION_REGION", "-DCLASS_REGION",
                   "-DNAMESPACE_REGION", "-DMACRO_REGION"]
    scope = analyze("scope", scope_flags, SCOPE_SOURCE)
    scope_regions = scope["conditional_regions"]
    assert [region["lexical_scope"] for region in scope_regions] == [
        "file", "non_file", "non_file", "non_file", "non_file",
        "non_file", "non_file", "non_file", "non_file", "non_file",
        "non_file", "non_file", "non_file", "non_file", "non_file"]
    scope_compile = subprocess.run(
        [CXX, "-std=c++17", "-I", str(ROOT), *scope_flags,
         "-fsyntax-only", str(ROOT / SCOPE_SOURCE)],
        text=True, capture_output=True)
    assert scope_compile.returncode == 0, scope_compile.stderr

    # A type used only as a pointer in an active class-body branch produces a
    # forward-declaration add whose provenance is the class-body condition.
    # This matches real HotSpot records: the condition is valid and active but
    # is not itself a legal file-scope insertion anchor.
    non_file = analyze(
        "non-file", ["-DNON_FILE_CONDITION"], NON_FILE_SOURCE)
    non_file_regions = {
        region["source_region_id"]: region
        for region in non_file["conditional_regions"]
    }
    non_file_add = next(
        operation for operation in non_file["operations"]
        if operation["subject_action"] == "add" and
           operation["item_kind"] == "forward_declaration" and
           operation["spelling"] == "class NonFileConditionalType;")
    non_file_condition = non_file_add["edit_condition"]
    assert non_file_condition["status"] == "known"
    non_file_path = non_file_condition["value"]["source_region_path"]
    assert [non_file_regions[region_id]["activity"]
            for region_id in non_file_path] == ["active"]
    assert [non_file_regions[region_id]["lexical_scope"]
            for region_id in non_file_path] == ["non_file"]
    assert non_file_add["multi_config"]["value"][0]["condition"] == \
        non_file_condition

    # A present include retained by an IWYU keep pragma is no-edit provenance.
    # Its active condition remains exact and schema-valid in function, class,
    # namespace, and declaration scopes even though none is an edit location.
    non_file_keeps = {
        scope: analyze("zero", ["-DNON_FILE_KEEP"], source)
        for scope, source in NON_FILE_KEEP_SOURCES.items()
    }
    for scope, record in non_file_keeps.items():
        source = NON_FILE_KEEP_SOURCES[scope]
        assert_non_file_keep(record, source)
        keep_compile = subprocess.run(
            [CXX, "-std=c++17", "-I", str(ROOT), "-DNON_FILE_KEEP",
             "-fsyntax-only", str(ROOT / source)],
            text=True, capture_output=True)
        assert keep_compile.returncode == 0, (scope, keep_compile.stderr)

    # Legal phase-2 splicing can separate the hash from every directive
    # keyword. The producer reports keyword offsets and physical locations,
    # and the driver independently validates the same exact source census.
    spliced_bytes = (ROOT / SPLICED_SOURCE).read_bytes()
    spliced_records = {
        "enabled": analyze("enabled", ["-DSPLICED_BRANCH"], SPLICED_SOURCE),
        "fallback": analyze("fallback", [], SPLICED_SOURCE),
    }
    expected_spliced = {
        "enabled": (["active", "inactive"], "jit.hpp", "branch-49-0-49"),
        "fallback": (["inactive", "active"], "generic.hpp",
                     "branch-49-1-156"),
    }
    for name, record in spliced_records.items():
        regions = record["conditional_regions"]
        assert [(region["directive_kind"], region["opening_offset"],
                 region["directive_offset"], region["body_begin_offset"],
                 region["body_end_offset"]["value"])
                for region in regions] == [
                    ("if", 49, 49, 87, 156),
                    ("else", 49, 156, 161, 234),
                ]
        assert [region["activity"] for region in regions] == \
            expected_spliced[name][0]
        assert [region["directive_location"]["value"] for region in regions] == [
            {"path": SPLICED_SOURCE, "line": 4, "column": 1},
            {"path": SPLICED_SOURCE, "line": 10, "column": 1},
        ]
        assert [region["opening_location"]["value"] for region in regions] == [
            {"path": SPLICED_SOURCE, "line": 4, "column": 1},
            {"path": SPLICED_SOURCE, "line": 4, "column": 1},
        ]
        assert spliced_bytes[49:51] == b"if"
        assert spliced_bytes[156:160] == b"else"
        assert spliced_bytes[234:239] == b"endif"
        add = next(operation for operation in record["operations"]
                   if operation["subject_action"] == "add")
        assert add["provider"]["selected"]["value"]["path"].endswith(
            expected_spliced[name][1])
        assert add["edit_condition"]["value"]["source_region_id"] == \
            expected_spliced[name][2]
        compile_result = subprocess.run(
            [CXX, "-std=c++17", "-I", str(ROOT),
             *(["-DSPLICED_BRANCH"] if name == "enabled" else []),
             "-fsyntax-only", str(ROOT / SPLICED_SOURCE)],
            text=True, capture_output=True)
        assert compile_result.returncode == 0, compile_result.stderr

    # A nested group appearing inside the first outer branch is encountered in
    # source order before the outer alternatives. Schema order instead groups
    # all branches by their opening offset. Preserve the exact parent links
    # while requiring that canonical producer order.
    nested_flags = ["-DOUTER_BRANCH", "-DINNER_ALTERNATIVE"]
    nested = analyze("nested", nested_flags, NESTED_SOURCE)
    nested_regions = nested["conditional_regions"]
    assert [(region["source_region_id"], region["directive_kind"],
             region["activity"])
            for region in nested_regions] == [
                ("branch-1-0-1", "if", "active"),
                ("branch-1-1-183", "elif", "inactive"),
                ("branch-1-2-244", "else", "inactive"),
                ("branch-27-0-27", "if", "inactive"),
                ("branch-27-1-81", "elif", "active"),
                ("branch-27-2-142", "else", "inactive"),
            ]
    assert [region["parent_source_region_id"]
            for region in nested_regions] == [
                {"status": "not_applicable", "value": None},
                {"status": "not_applicable", "value": None},
                {"status": "not_applicable", "value": None},
                {"status": "known", "value": "branch-1-0-1"},
                {"status": "known", "value": "branch-1-0-1"},
                {"status": "known", "value": "branch-1-0-1"},
            ]
    nested_compile = subprocess.run(
        [CXX, "-std=c++17", *nested_flags, "-fsyntax-only",
         str(ROOT / NESTED_SOURCE)], text=True, capture_output=True)
    assert nested_compile.returncode == 0, nested_compile.stderr

    # A real included-header guard is semantic framing, not a configuration
    # condition. It remains the lexical root of a nested branch anchor so the
    # exact source path resolves through the complete census. Operations inside
    # only the guard stay unconditional; nested add/remove operations carry the
    # guard plus target branch but only the target expression.
    guarded_flags = ["-DGUARDED_FEATURE"]
    guarded = analyze(
        "guarded", guarded_flags, GUARDED_SOURCE, GUARDED_HEADER,
        ("--check_also=" + GUARDED_HEADER,))
    guarded_regions = guarded["conditional_regions"]
    assert [(region["source_region_id"],
             region["parent_source_region_id"],
             region["directive_kind"], region["expression"],
             region["activity"], region["lexical_scope"])
            for region in guarded_regions] == [
        ("branch-1-0-1", {"status": "not_applicable", "value": None},
         "ifndef", "!defined(TESTS_CONDITIONAL_REGIONS_GUARDED_HPP_)",
         "active", "file"),
        ("branch-199-0-199", {"status": "known",
                              "value": "branch-1-0-1"},
         "if", "defined(GUARDED_FEATURE)", "active", "file"),
    ]
    guarded_operations = {
        (operation["subject_action"], operation["spelling"]): operation
        for operation in guarded["operations"]
    }
    nested_keys = {
        ("remove", '#include "tests/conditional_regions/all.hpp"'),
        ("add", '#include "tests/conditional_regions/jit.hpp"'),
    }
    guard_only_keys = {
        ("remove", '#include "tests/cxx/direct.h"'),
        ("add", '#include "tests/cxx/indirect.h"'),
    }
    assert set(guarded_operations) == nested_keys | guard_only_keys
    nested_condition = {
        "status": "known",
        "value": {
            "expression": "defined(GUARDED_FEATURE)",
            "location": {
                "status": "known",
                "value": {"path": GUARDED_HEADER, "line": 10, "column": 2},
            },
            "active_branch": True,
            "source_region_id": "branch-199-0-199",
            "source_region_path": ["branch-1-0-1", "branch-199-0-199"],
        },
    }
    for key in nested_keys:
        operation = guarded_operations[key]
        assert operation["edit_condition"] == nested_condition
        assert operation["multi_config"]["value"][0]["condition"] == \
            nested_condition
    for key in guard_only_keys:
        operation = guarded_operations[key]
        assert operation["edit_condition"] == {
            "status": "not_applicable", "value": None}
        assert operation["multi_config"]["value"][0]["condition"] == {
            "status": "not_applicable", "value": None}

    guard_policy = """schema_version: 1
rules:
  - id: nested-condition-without-guard
    action: deny_add
    match:
      includee: { glob: "*tests/conditional_regions/jit.hpp" }
      active_condition: "defined(GUARDED_FEATURE)"
  - id: guard-is-not-an-active-condition
    action: deny_add
    match:
      includee: { glob: "*tests/cxx/indirect.h" }
      active_condition: "!defined(TESTS_CONDITIONAL_REGIONS_GUARDED_HPP_)"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", dir=ROOT,
                                     delete=False) as policy_stream:
        policy_stream.write(guard_policy)
        policy_path = Path(policy_stream.name).resolve()
    try:
        guarded_policy = analyze(
            "guarded", guarded_flags, GUARDED_SOURCE, GUARDED_HEADER,
            ("--check_also=" + GUARDED_HEADER,
             "--project_policy=" + str(policy_path)))
    finally:
        policy_path.unlink()
    guarded_policy_operations = {
        (operation["subject_action"], operation["spelling"]): operation
        for operation in guarded_policy["operations"]
    }
    nested_add = guarded_policy_operations[
        ("add", '#include "tests/conditional_regions/jit.hpp"')]
    assert nested_add["reason_code"] == "project_policy_deny_add"
    assert nested_add["policy"]["value"]["rule_id"] == \
        "nested-condition-without-guard"
    guard_only_add = guarded_policy_operations[
        ("add", '#include "tests/cxx/indirect.h"')]
    assert guard_only_add["disposition"] == "apply"
    assert guard_only_add["policy"]["status"] == "unavailable"

    guarded_compile = subprocess.run(
        [CXX, "-std=c++17", "-I", str(ROOT), *guarded_flags,
         "-fsyntax-only", str(ROOT / GUARDED_SOURCE)],
        text=True, capture_output=True)
    assert guarded_compile.returncode == 0, guarded_compile.stderr

    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary)
        environment = os.environ.copy()
        environment["IWYU_BINARY"] = str(IWYU)

        keep_entries = [{
            "directory": str(ROOT),
            "file": source,
            "arguments": [CXX, "-I", str(ROOT), "-std=c++17",
                          "-DNON_FILE_KEEP", "-c", source],
        } for source in NON_FILE_KEEP_SOURCES.values()]
        (database / "compile_commands.json").write_text(
            json.dumps(keep_entries))
        keep_driven = subprocess.run(
            [sys.executable, str(DRIVER), "-o", "json", "--output-schema",
             "2", "--database", "zero=" + str(database)],
            cwd=ROOT, env=environment, text=True, capture_output=True)
        assert keep_driven.returncode == 0, keep_driven.stderr
        assert keep_driven.stderr == ""
        keep_driven_records = [
            json.loads(line) for line in keep_driven.stdout.splitlines()]
        assert len(keep_driven_records) == len(NON_FILE_KEEP_SOURCES)
        keep_records_by_name = {
            Path(record["file"]["path"]).name: record
            for record in keep_driven_records
        }
        assert set(keep_records_by_name) == {
            Path(source).name for source in NON_FILE_KEEP_SOURCES.values()}
        for scope, source in NON_FILE_KEEP_SOURCES.items():
            record = keep_records_by_name[Path(source).name]
            VALIDATOR.validate(record)
            assert_non_file_keep(record, source)

        non_file_entry = {
            "directory": str(ROOT),
            "file": NON_FILE_SOURCE,
            "arguments": [CXX, "-I", str(ROOT), "-std=c++17",
                          "-DNON_FILE_CONDITION", "-c", NON_FILE_SOURCE],
        }
        (database / "compile_commands.json").write_text(
            json.dumps([non_file_entry]))
        non_file_driven = subprocess.run(
            [sys.executable, str(DRIVER), "-o", "json", "--output-schema",
             "2", "--database", "non-file=" + str(database)],
            cwd=ROOT, env=environment, text=True, capture_output=True)
        assert non_file_driven.returncode == 0, non_file_driven.stderr
        assert non_file_driven.stderr == ""
        non_file_driven_records = [
            json.loads(line)
            for line in non_file_driven.stdout.splitlines()]
        assert len(non_file_driven_records) == 1
        driven_non_file = non_file_driven_records[0]
        driven_regions = {
            region["source_region_id"]: region
            for region in driven_non_file["conditional_regions"]
        }
        driven_add = next(
            operation for operation in driven_non_file["operations"]
            if operation["subject_action"] == "add" and
               operation["item_kind"] == "forward_declaration")
        driven_path = driven_add["edit_condition"]["value"][
            "source_region_path"]
        assert [driven_regions[region_id]["lexical_scope"]
                for region_id in driven_path] == ["non_file"]
        assert driven_add["multi_config"]["value"][0]["condition"] == \
            driven_add["edit_condition"]

        entry = {
            "directory": str(ROOT),
            "file": SPLICED_SOURCE,
            "arguments": [CXX, "-I", str(ROOT), "-std=c++17",
                          "-DSPLICED_BRANCH", "-c", SPLICED_SOURCE],
        }
        (database / "compile_commands.json").write_text(json.dumps([entry]))
        driven = subprocess.run(
            [sys.executable, str(DRIVER), "-o", "json", "--output-schema",
             "2", "--database", "enabled=" + str(database)],
            cwd=ROOT, env=environment, text=True, capture_output=True)
        assert driven.returncode == 0, driven.stderr
        assert driven.stderr == ""
        driven_records = [json.loads(line) for line in driven.stdout.splitlines()]
        assert len(driven_records) == 1
        driven_record = driven_records[0]
        VALIDATOR.validate(driven_record)
        assert driven_record["conditional_regions"] == \
            spliced_records["enabled"]["conditional_regions"]

        nested_entry = {
            "directory": str(ROOT),
            "file": NESTED_SOURCE,
            "arguments": [CXX, "-std=c++17", *nested_flags,
                          "-c", NESTED_SOURCE],
        }
        (database / "compile_commands.json").write_text(
            json.dumps([nested_entry]))
        nested_driven = subprocess.run(
            [sys.executable, str(DRIVER), "-o", "json", "--output-schema",
             "2", "--database", "nested=" + str(database)],
            cwd=ROOT, env=environment, text=True, capture_output=True)
        assert nested_driven.returncode == 0, nested_driven.stderr
        assert nested_driven.stderr == ""
        nested_driven_records = [
            json.loads(line) for line in nested_driven.stdout.splitlines()]
        assert len(nested_driven_records) == 1
        nested_driven_record = nested_driven_records[0]
        VALIDATOR.validate(nested_driven_record)
        assert nested_driven_record["conditional_regions"] == nested_regions

        guarded_entry = {
            "directory": str(ROOT),
            "file": GUARDED_SOURCE,
            "arguments": [CXX, "-I", str(ROOT), "-std=c++17",
                          *guarded_flags, "-c", GUARDED_SOURCE],
        }
        (database / "compile_commands.json").write_text(
            json.dumps([guarded_entry]))
        guarded_driven = subprocess.run(
            [sys.executable, str(DRIVER), "-o", "json", "--output-schema",
             "2", "--database", "guarded=" + str(database)],
            cwd=ROOT, env=environment, text=True, capture_output=True)
        assert guarded_driven.returncode == 0, guarded_driven.stderr
        assert guarded_driven.stderr == ""
        guarded_driven_records = [
            json.loads(line) for line in guarded_driven.stdout.splitlines()]
        guarded_driven_record = next(
            record for record in guarded_driven_records
            if record["file"]["path"].endswith("guarded.hpp"))
        VALIDATOR.validate(guarded_driven_record)
        assert [(region["source_region_id"],
                 region["parent_source_region_id"],
                 region["directive_kind"], region["expression"],
                 region["activity"], region["lexical_scope"])
                for region in guarded_driven_record["conditional_regions"]] == [
            (region["source_region_id"], region["parent_source_region_id"],
             region["directive_kind"], region["expression"],
             region["activity"], region["lexical_scope"])
            for region in guarded_regions
        ]
        driven_operations = {
            (operation["subject_action"], operation["spelling"]): operation
            for operation in guarded_driven_record["operations"]
        }
        assert set(driven_operations) == nested_keys | guard_only_keys
        driven_nested_condition = json.loads(json.dumps(nested_condition))
        driven_nested_condition["value"]["location"]["value"]["path"] = \
            str((ROOT / GUARDED_HEADER).resolve()).replace(os.sep, "/")
        for key in nested_keys:
            assert driven_operations[key]["edit_condition"] == \
                driven_nested_condition
            assert driven_operations[key]["multi_config"]["value"][0][
                "condition"] == driven_nested_condition
        for key in guard_only_keys:
            assert driven_operations[key]["edit_condition"] == {
                "status": "not_applicable", "value": None}

        # An applied remove/include condition anchors the present include
        # occurrence. The producer guarantees that this actual edit site is
        # active file scope, so a claimed non-file occurrence must still fail
        # protocol validation. No-edit remove/keep operations above are valid.
        forged_removal_scope = copy.deepcopy(guarded_driven_record)
        forged_operation = driven_operations[
            ("remove", '#include "tests/conditional_regions/all.hpp"')]
        assert (forged_operation["item_kind"],
                forged_operation["disposition"]) == ("include", "apply")
        forged_target = next(
            region for region in forged_removal_scope["conditional_regions"]
            if region["source_region_id"] == "branch-199-0-199")
        forged_target["lexical_scope"] = "non_file"
        driver_spec = importlib.util.spec_from_file_location(
            "conditional_regions_iwyu_tool", DRIVER)
        driver_module = importlib.util.module_from_spec(driver_spec)
        driver_spec.loader.exec_module(driver_module)
        try:
            driver_module._validate_declaration_facts(
                forged_removal_scope, (ROOT / GUARDED_HEADER).read_bytes())
        except driver_module.ProtocolError as error:
            assert "not entirely active file scope" in str(error)
        else:
            raise AssertionError(
                "conditional include removal accepted a non-file edit anchor")

    print("PASS: MC-002 complete conditional-region census and scope facts")


if __name__ == "__main__":
    main()
