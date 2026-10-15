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
    result = run([
        IWYU, "-Xiwyu", "--output_format=json", "-std=c++17",
        "-I", include_argument, "-x", "c++-header", source_argument,
    ], cwd=root)
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 1, records
    return records[0]


def assert_initial_contracts():
    expected_names = set(COMPLETE) | set(INCOMPLETE) | {
        "provider.hpp", "complete_use.cc"}
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


def compile_tree(corpus):
    for filename in sorted(set(COMPLETE) | set(INCOMPLETE)):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".cc", dir=corpus.parent,
                delete=False) as stream:
            stream.write(f'#include "{filename}"\n')
            probe = Path(stream.name)
        try:
            run([CXX, "-std=c++17", "-fsyntax-only", "-I", corpus, probe])
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

        for filename in sorted(set(COMPLETE) | set(INCOMPLETE)):
            source = temporary_corpus / filename
            analysis = run([
                IWYU, "-std=c++17", "-I", temporary_corpus,
                "-x", "c++-header", source,
            ], cwd=temporary_root)
            run([
                sys.executable, ROOT / "fix_includes.py", "--nosafe_headers",
                "--nocomments", "--noblank_lines",
            ], cwd=temporary_root, stdin=analysis.stderr)

        for filename in COMPLETE:
            assert (temporary_corpus / filename).read_bytes() == (
                CORPUS / filename).read_bytes(), filename
        for filename in INCOMPLETE:
            assert (temporary_corpus / filename).read_bytes() == (
                CORPUS / "expected" / filename).read_bytes(), filename

        compile_tree(temporary_corpus)

        for filename in sorted(set(COMPLETE) | set(INCOMPLETE)):
            record = analyze_json(
                filename, root=temporary_root, corpus=temporary_corpus)
            assert record["status"] == "correct", (filename, record)
            assert record["operations"] == [], (filename, record["operations"])


def main():
    assert_initial_contracts()
    compile_tree(CORPUS)
    assert_fixed_tree()
    print("SEM-001 complete-use analyzer corpus passed")


if __name__ == "__main__":
    main()
