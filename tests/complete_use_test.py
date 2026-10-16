#!/usr/bin/env python3
"""Opaque analyzer-core contracts for complete versus incomplete type use."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "complete_use"
IWYU = Path(sys.argv[1]).resolve()
CXX = Path(sys.argv[2]).resolve()

COMPLETE = {
    "accessor_dereference.hpp": "AccessorDereference",
    "base_class.hpp": "BaseClass",
    "by_value_field.hpp": "ByValueField",
    "by_value_parameter.hpp": "ByValueParameter",
    "by_value_return.hpp": "ByValueReturn",
    "new_type.hpp": "NewType",
    "pointer_dereference.hpp": "PointerDereference",
    "reference_member_access.hpp": "ReferenceMemberAccess",
    "scoped_member.hpp": "ScopedMember",
    "sizeof_type.hpp": "SizeofType",
}
INCOMPLETE = {
    "declarations.hpp": [
        ("PointerDeclaration", 4, 7, 3),
        ("ReferenceDeclaration", 5, 8, 3),
    ],
    "pointer_template_argument.hpp": [
        ("PointerTemplateArgument", 28, 9, 5),
    ],
}
MACRO_COMPLETE = {
    "macro_argument.hpp": ("MacroArgumentValue", "macro_provider.hpp"),
    "macro_body.hpp": ("MacroBodyValue", "macro_provider.hpp"),
}
ORDER_SENSITIVE = {
    "include_after_use.hpp": (
        "IncludeAfterUse", "include_after_use_provider.hpp"),
    "conditional_provider.hpp": (
        "ConditionalProviderValue", "conditional_provider_definition.hpp"),
}
MATRIX = "by_value_type_matrix.hpp"
MATRIX_COMPLETE = {
    "MatrixByValueParameter", "MatrixByValueReturn", "MatrixByValueBox"}
MATRIX_INCOMPLETE = {
    "MatrixRvalueParameter", "MatrixRvalueReturn", "MatrixMemberPointee",
    "MatrixMemberOwner", "MatrixAdjustedFunctionReturn",
    "MatrixArrayParameter", "MatrixLvalueParameter", "MatrixLvalueReturn",
    "MatrixPointerParameter", "MatrixPointerReturn",
    "MatrixNestedValueArgument",
}
SUPPORT = {
    "provider.hpp", "complete_use.cc", "value_shapes_provider.hpp",
    "incomplete_shapes_provider.hpp", "include_after_use_provider.hpp",
    "conditional_provider_definition.hpp", "macro_provider.hpp",
    "macro_cross_definition.hpp", "macro_cross_provider.hpp",
}
TARGETS = (set(COMPLETE) | set(INCOMPLETE) | set(MACRO_COMPLETE) |
           set(ORDER_SENSITIVE) | {MATRIX, "macro_cross_file.hpp"})


def run(command, cwd=ROOT, stdin=None):
    result = subprocess.run(
        [str(argument) for argument in command], cwd=cwd, input=stdin,
        text=True, capture_output=True)
    assert result.returncode == 0, (
        f"command failed ({result.returncode}): {command}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def unavailable(status):
    return {"status": status, "value": None}


def condition(filename):
    guard = "TESTS_COMPLETE_USE_" + filename[:-4].upper() + "_HPP_"
    return {
        "status": "known",
        "value": {
            "active_branch": True,
            "expression": f"!defined({guard})",
            "location": {
                "status": "known",
                "value": {
                    "column": 2,
                    "line": 1,
                    "path": f"tests/complete_use/{filename}",
                },
            },
        },
    }


def provider():
    return {
        "candidates": unavailable("not_applicable"),
        "chain": unavailable("not_applicable"),
        "selected": unavailable("not_applicable"),
    }


def remove_operation(filename):
    return {
        "disposition": "apply",
        "edit_condition": condition(filename),
        "id": "op-1",
        "item_kind": "include",
        "multi_config": unavailable("not_applicable"),
        "policy": unavailable("unavailable"),
        "provider": provider(),
        "reason": "Present item is absent from IWYU's desired state.",
        "reason_code": "undesired_item_present",
        "source_range": unavailable("unavailable"),
        "spelling": '#include "provider.hpp"',
        "subject_action": "remove",
        "symbols": [],
    }


def add_operation(filename, index, symbol, declaration_line, use_line,
                  use_column):
    return {
        "disposition": "apply",
        "edit_condition": condition(filename),
        "id": f"op-{index}",
        "item_kind": "forward_declaration",
        "multi_config": unavailable("not_applicable"),
        "policy": unavailable("unavailable"),
        "provider": provider(),
        "reason": "IWYU's desired item is absent from the file.",
        "reason_code": "desired_item_missing",
        "source_range": unavailable("not_applicable"),
        "spelling": f"class {symbol};",
        "subject_action": "add",
        "symbols": [{
            "declaration": {
                "status": "known",
                "value": {
                    "column": 7,
                    "line": declaration_line,
                    "path": "tests/complete_use/provider.hpp",
                },
            },
            "name": symbol,
            "qualified_name": {"status": "known", "value": symbol},
            "uses": [{
                "condition": condition(filename),
                "location": {
                    "column": use_column,
                    "line": use_line,
                    "path": f"tests/complete_use/{filename}",
                },
                "macro": unavailable("unavailable"),
                "origin": "unknown",
                "requirement": "forward_declaration",
            }],
        }],
    }


def analyze_json(filename, root=ROOT, corpus=CORPUS):
    source = corpus / filename
    source_argument = source.relative_to(root)
    include_argument = corpus.relative_to(root)
    defines = ["-DFLAG=1"] if filename == "conditional_provider.hpp" else []
    result = run([
        IWYU, "-Xiwyu", "--output_format=json", "-std=c++17", *defines,
        "-I", include_argument, "-x", "c++-header", source_argument,
    ], cwd=root)
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 1, records
    return records[0]


def analyze_macro_cross_file(root=ROOT, corpus=CORPUS):
    source = corpus / "macro_cross_file.hpp"
    checked = corpus / "macro_cross_definition.hpp"
    result = run([
        IWYU, "-Xiwyu", "--output_format=json",
        "-Xiwyu", f"--check_also={checked.relative_to(root)}",
        "-std=c++17", "-I", corpus.relative_to(root),
        "-x", "c++-header", source.relative_to(root),
    ], cwd=root)
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 2, records
    return {record["file"]["path"]: record for record in records}


def item_spellings(record, kind):
    return {
        item["spelling"] for item in record["desired_state"]["items"]
        if item["item_kind"] == kind
    }


def operation_spellings(record, action, kind):
    return {
        operation["spelling"] for operation in record["operations"]
        if operation["subject_action"] == action and
        operation["item_kind"] == kind
    }


def assert_initial_contracts():
    expected_names = TARGETS | SUPPORT
    actual_names = {
        path.name for path in CORPUS.iterdir()
        if path.is_file()
    }
    assert actual_names == expected_names, (actual_names, expected_names)

    for filename, symbol in COMPLETE.items():
        record = analyze_json(filename)
        assert record["file"] == {
            "kind": "header",
            "path": f"tests/complete_use/{filename}",
        }
        assert record["status"] == "correct"
        assert record["operations"] == []
        assert record["desired_state"] == {
            "complete": True,
            "items": [{
                "item_kind": "include",
                "spelling": '#include "provider.hpp"',
                "symbols": [symbol],
            }],
            "status": "known",
        }

    for filename, symbols in INCOMPLETE.items():
        record = analyze_json(filename)
        expected_operations = [remove_operation(filename)]
        expected_operations.extend(
            add_operation(filename, index + 2, *symbol)
            for index, symbol in enumerate(symbols))
        assert record["status"] == "changes"
        assert record["operations"] == expected_operations
        assert record["desired_state"] == {
            "complete": True,
            "items": [{
                "item_kind": "forward_declaration",
                "spelling": f"class {symbol[0]};",
                "symbols": [],
            } for symbol in symbols],
            "status": "known",
        }

    for filename, (symbol, provider_header) in MACRO_COMPLETE.items():
        record = analyze_json(filename)
        assert record["status"] == "correct", (filename, record)
        assert record["operations"] == [], (filename, record["operations"])
        assert item_spellings(record, "include") == {
            f'#include "{provider_header}"'}, record["desired_state"]
        assert item_spellings(record, "forward_declaration") == set(), record
        include_item = record["desired_state"]["items"][0]
        assert include_item["symbols"] == [symbol], include_item

    records = analyze_macro_cross_file()
    definition_record = records[
        "tests/complete_use/macro_cross_definition.hpp"]
    expansion_record = records["tests/complete_use/macro_cross_file.hpp"]
    for record in (definition_record, expansion_record):
        assert record["status"] == "correct", record
        assert record["operations"] == [], record["operations"]
    assert item_spellings(definition_record, "include") == {
        '#include "macro_cross_provider.hpp"'}, definition_record
    assert definition_record["desired_state"]["items"][0]["symbols"] == [
        "MacroCrossValue"], definition_record
    assert item_spellings(expansion_record, "include") == {
        '#include "macro_cross_definition.hpp"'}, expansion_record

    for filename, (symbol, provider_header) in ORDER_SENSITIVE.items():
        record = analyze_json(filename)
        assert record["status"] == "changes", (filename, record)
        assert item_spellings(record, "include") == set(), (
            filename, record["desired_state"])
        assert item_spellings(record, "forward_declaration") == {
            f"class {symbol};"}, record["desired_state"]
        assert operation_spellings(record, "remove", "include") == {
            f'#include "{provider_header}"'}, record["operations"]
        assert operation_spellings(
            record, "remove", "forward_declaration") == set(), (
                filename, record["operations"])
        assert operation_spellings(
            record, "add", "forward_declaration") == set(), record["operations"]

    record = analyze_json(MATRIX)
    assert record["status"] == "changes", record
    assert item_spellings(record, "include") == {
        '#include "value_shapes_provider.hpp"'}, record["desired_state"]
    assert item_spellings(record, "forward_declaration") == {
        f"class {symbol};" for symbol in MATRIX_INCOMPLETE
    }, record["desired_state"]
    assert operation_spellings(record, "remove", "include") == {
        '#include "incomplete_shapes_provider.hpp"'}, record["operations"]
    assert operation_spellings(record, "add", "forward_declaration") == {
        f"class {symbol};" for symbol in MATRIX_INCOMPLETE
    }, record["operations"]
    desired_include = next(
        item for item in record["desired_state"]["items"]
        if item["item_kind"] == "include")
    assert set(desired_include["symbols"]) == MATRIX_COMPLETE, desired_include


def compile_tree(corpus):
    for filename in sorted(TARGETS):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".cc", dir=corpus.parent,
                delete=False) as stream:
            stream.write(f'#include "{filename}"\n')
            probe = Path(stream.name)
        try:
            extra = (
                ["-DFLAG=1"] if filename == "conditional_provider.hpp"
                else [])
            run([CXX, "-std=c++17", "-fsyntax-only", *extra,
                 "-I", corpus, probe])
            if filename == "conditional_provider.hpp":
                run([CXX, "-std=c++17", "-fsyntax-only", "-DFLAG=0",
                     "-I", corpus, probe])
        finally:
            probe.unlink()

    run([
        CXX, "-std=c++17", "-fsyntax-only", "-I", corpus,
        corpus / "complete_use.cc",
    ])


def assert_fixed_tree():
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sem001-", dir=build) as tmp:
        temporary_root = Path(tmp)
        temporary_corpus = temporary_root / "complete_use"
        shutil.copytree(CORPUS, temporary_corpus)

        for filename in sorted(TARGETS):
            source = temporary_corpus / filename
            defines = (
                ["-DFLAG=1"] if filename == "conditional_provider.hpp"
                else [])
            analysis = run([
                IWYU, "-std=c++17", *defines, "-I", temporary_corpus,
                "-x", "c++-header", source,
            ], cwd=temporary_root)
            run([
                sys.executable, ROOT / "fix_includes.py", "--nosafe_headers",
                "--nocomments", "--noblank_lines",
            ], cwd=temporary_root, stdin=analysis.stderr)

        cross_source = temporary_corpus / "macro_cross_file.hpp"
        cross_definition = temporary_corpus / "macro_cross_definition.hpp"
        cross_analysis = run([
            IWYU, "-Xiwyu", f"--check_also={cross_definition}",
            "-std=c++17", "-I", temporary_corpus,
            "-x", "c++-header", cross_source,
        ], cwd=temporary_root)
        run([
            sys.executable, ROOT / "fix_includes.py", "--nosafe_headers",
            "--nocomments", "--noblank_lines",
        ], cwd=temporary_root, stdin=cross_analysis.stderr)

        unchanged = (set(COMPLETE) | set(MACRO_COMPLETE) |
                     {"macro_cross_file.hpp", "macro_cross_definition.hpp"})
        for filename in unchanged:
            assert (temporary_corpus / filename).read_bytes() == (
                CORPUS / filename).read_bytes(), filename
        transformed = set(INCOMPLETE) | set(ORDER_SENSITIVE) | {MATRIX}
        for filename in transformed:
            assert (temporary_corpus / filename).read_bytes() == (
                CORPUS / "expected" / filename).read_bytes(), filename

        compile_tree(temporary_corpus)

        for filename in sorted(TARGETS):
            record = analyze_json(
                filename, root=temporary_root, corpus=temporary_corpus)
            assert record["status"] == "correct", (filename, record)
            assert record["operations"] == [], (filename, record["operations"])

        for filename, record in analyze_macro_cross_file(
                root=temporary_root, corpus=temporary_corpus).items():
            assert record["status"] == "correct", (filename, record)
            assert record["operations"] == [], (filename, record["operations"])


def main():
    assert_initial_contracts()
    compile_tree(CORPUS)
    assert_fixed_tree()
    print("SEM-001 complete-use analyzer corpus passed")


if __name__ == "__main__":
    main()
