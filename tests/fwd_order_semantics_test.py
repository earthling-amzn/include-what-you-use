#!/usr/bin/env python3

##===--- fwd_order_semantics_test.py - provider order contracts -----------===##
#
#                     The LLVM Compiler Infrastructure
#
# This file is distributed under the University of Illinois Open Source
# License. See LICENSE.TXT for details.
#
##===----------------------------------------------------------------------===##

"""Opaque FWD-003/FWD-004 source-order and provider-edge contracts."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fwd_order"
IWYU = Path(sys.argv[1]).resolve()
CXX = Path(sys.argv[2]).resolve()


def include(name, symbols=(), keep=False):
    spelling = f'#include "tests/fwd_order/{name}"'
    if keep:
        spelling += "  // IWYU pragma: keep"
    return {
        "item_kind": "include",
        "spelling": spelling,
        "symbols": list(symbols),
    }


def declaration(spelling):
    return {
        "item_kind": "forward_declaration",
        "spelling": spelling,
        "symbols": [],
    }


def operation(action, kind, spelling, disposition="apply",
              reason="undesired_item_present"):
    return (action, kind, spelling, disposition, reason)


CASES = [
    {
        "name": "reentrant-early",
        "source": "fwd003_early.cc",
        "target": "fwd003_early.hpp",
        "configuration": "fwd003-early",
        "decl": "class Fwd003Value;",
        "present": False,
        "operations": [operation(
            "remove", "forward_declaration", "class Fwd003Value;")],
        "items": [include(
            "fwd003_later.hpp", ["Fwd003Value (ptr only)"])],
    },
    {
        "name": "reentrant-later",
        "source": "fwd003_later.cc",
        "target": "fwd003_early.hpp",
        "configuration": "fwd003-later",
        "decl": "class Fwd003Value;",
        "present": True,
        "operations": [operation(
            "remove", "include",
            '#include "tests/fwd_order/fwd003_later.hpp"  '
            "// IWYU pragma: keep", "keep", "pragma_keep")],
        "items": [
            include("fwd003_later.hpp", keep=True),
            declaration("class Fwd003Value;"),
        ],
    },
    {
        "name": "associated-direct",
        "source": "fwd004_associated.cc",
        "target": "fwd004_associated.cc",
        "decl": "class Fwd004Associated;",
        "present": False,
        "operations": [operation(
            "remove", "forward_declaration", "class Fwd004Associated;")],
        "items": [include("fwd004_associated.hpp")],
    },
    {
        "name": "associated-conditional",
        "source": "fwd004_associated_conditional.cc",
        "target": "fwd004_associated_conditional.cc",
        "decl": "class Fwd004Associated;",
        "present": True,
        "args": ["-DFWD004_ENABLE_PROVIDER=1"],
        "operations": [],
        "items": [
            include("fwd004_associated_conditional.hpp"),
            declaration("class Fwd004Associated;"),
        ],
    },
    {
        "name": "associated-different-instance",
        "source": "fwd004_associated_instance.cc",
        "target": "fwd004_associated_instance.cc",
        "decl": "class Fwd004Associated;",
        "present": True,
        "operations": [operation(
            "remove", "include",
            '#include "tests/fwd_order/fwd004_associated_seed.hpp"')],
        "items": [
            include("fwd004_associated_instance.hpp"),
            declaration("class Fwd004Associated;"),
        ],
    },
    {
        "name": "associated-late",
        "source": "fwd004_associated_late.cc",
        "target": "fwd004_associated_late.cc",
        "decl": "class Fwd004Associated;",
        "present": True,
        "operations": [],
        "items": [
            include("fwd004_associated.hpp"),
            declaration("class Fwd004Associated;"),
        ],
    },
    {
        "name": "mapped-direct",
        "source": "fwd004_mapped.cc",
        "target": "fwd004_mapped.cc",
        "decl": "class Fwd004Mapped;",
        "present": False,
        "mapping": "fwd004_mapped.imp",
        "operations": [operation(
            "remove", "forward_declaration", "class Fwd004Mapped;")],
        "items": [include(
            "fwd004_mapped_public.hpp", ["Fwd004Mapped (ptr only)"])],
    },
    {
        "name": "mapped-conditional",
        "source": "fwd004_mapped_conditional.cc",
        "target": "fwd004_mapped_conditional.cc",
        "decl": "class Fwd004MappedConditional;",
        "present": True,
        "args": ["-DFWD004_ENABLE_PROVIDER=1"],
        "mapping": "fwd004_mapped_conditional.imp",
        "operations": [operation(
            "remove", "include",
            '#include "tests/fwd_order/'
            'fwd004_mapped_conditional_public.hpp"  '
            "// IWYU pragma: keep", "keep", "pragma_keep")],
        "items": [
            include("fwd004_mapped_conditional_public.hpp", keep=True),
            declaration("class Fwd004MappedConditional;"),
        ],
    },
    {
        "name": "mapped-deeper",
        "source": "fwd004_mapped_deep.cc",
        "target": "fwd004_mapped_deep.cc",
        "decl": "class Fwd004MappedDeep;",
        "present": True,
        "mapping": "fwd004_mapped_deep.imp",
        "operations": [operation(
            "remove", "include",
            '#include "tests/fwd_order/fwd004_mapped_deep_public.hpp"  '
            "// IWYU pragma: keep", "keep", "pragma_keep")],
        "items": [
            include("fwd004_mapped_deep_public.hpp", keep=True),
            declaration("class Fwd004MappedDeep;"),
        ],
    },
    {
        "name": "mapped-late",
        "source": "fwd004_mapped_late.cc",
        "target": "fwd004_mapped_late.cc",
        "decl": "class Fwd004Mapped;",
        "present": True,
        "mapping": "fwd004_mapped.imp",
        "operations": [operation(
            "remove", "include",
            '#include "tests/fwd_order/fwd004_mapped_public.hpp"  '
            "// IWYU pragma: keep", "keep", "pragma_keep")],
        "items": [
            include("fwd004_mapped_public.hpp", keep=True),
            declaration("class Fwd004Mapped;"),
        ],
    },
    {
        "name": "mapped-ambiguous",
        "source": "fwd004_mapped_ambiguous.cc",
        "target": "fwd004_mapped_ambiguous.cc",
        "decl": "class Fwd004MappedAmbiguous;",
        "present": True,
        "mapping": "fwd004_mapped_ambiguous.imp",
        "operations": [operation(
            "remove", "include",
            '#include "tests/fwd_order/'
            'fwd004_mapped_ambiguous_public.hpp"  '
            "// IWYU pragma: keep", "keep", "pragma_keep")],
        "items": [
            include("fwd004_mapped_ambiguous_public.hpp", keep=True),
            declaration("class Fwd004MappedAmbiguous;"),
        ],
    },
]


def analyzer_command(case, root, json_output=False):
    command = [str(IWYU)]
    if json_output:
        command.extend(["-Xiwyu", "--output_format=json"])
    command.extend([
        "-Xiwyu", "--check_also=tests/fwd_order/*.hpp",
        "-I", ".", "-std=c++17",
    ])
    if case.get("configuration"):
        command.extend([
            "-Xiwyu", f"--configuration={case['configuration']}"])
    command.extend(case.get("args", []))
    if case.get("mapping"):
        command.extend([
            "-Xiwyu",
            f"--mapping_file=tests/fwd_order/{case['mapping']}",
        ])
    command.append(f"tests/fwd_order/{case['source']}")
    return command


def analyze(case, root, json_output=False):
    return subprocess.run(
        analyzer_command(case, root, json_output), cwd=root,
        text=True, capture_output=True)


def target_record(case, root):
    result = analyze(case, root, json_output=True)
    assert result.returncode == 0, (case["name"], result.stderr)
    assert result.stderr == "", (case["name"], result.stderr)
    records = [json.loads(line) for line in result.stdout.splitlines()]
    target = f"tests/fwd_order/{case['target']}"
    matches = [record for record in records if record["file"]["path"] == target]
    assert len(matches) == 1, (case["name"], target, records)
    return matches[0]


def operation_tuples(record):
    return [
        (op["subject_action"], op["item_kind"], op["spelling"],
         op["disposition"], op["reason_code"])
        for op in record["operations"]
    ]


def compile_case(case, root):
    command = [str(CXX), "-std=c++17", "-fsyntax-only", "-I", "."]
    command.extend(arg for arg in case.get("args", []) if arg.startswith("-D"))
    command.append(f"tests/fwd_order/{case['source']}")
    result = subprocess.run(command, cwd=root, text=True, capture_output=True)
    assert result.returncode == 0, (
        case["name"], result.stdout + result.stderr)


def tree_digest(root):
    digest = hashlib.sha256()
    for path in sorted((root / "tests" / "fwd_order").rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def assert_text_contract(case, root):
    result = analyze(case, root)
    assert result.returncode == 0, (case["name"], result.stderr)
    assert result.stdout == "", (case["name"], result.stdout)
    removal = f"- {case['decl']}  // lines "
    target = f"tests/fwd_order/{case['target']}"
    if case["present"]:
        assert removal not in result.stderr, (case["name"], result.stderr)
        correct = f"({target} has correct #includes/fwd-decls)"
        full_list = f"The full include-list for {target}:\n"
        assert correct in result.stderr or (
            full_list in result.stderr and case["decl"] in result.stderr), (
                case["name"], result.stderr)
    else:
        assert removal in result.stderr, (case["name"], result.stderr)
        full_list = result.stderr.split(
            f"The full include-list for {target}:\n", 1)[1].split("---\n", 1)[0]
        assert case["decl"] not in full_list, (case["name"], full_list)
    return result.stderr


def assert_initial_contract(case):
    compile_case(case, ROOT)
    record = target_record(case, ROOT)
    assert operation_tuples(record) == case["operations"], (
        case["name"], operation_tuples(record))
    assert record["desired_state"] == {
        "status": "known", "complete": True, "items": case["items"]}, (
            case["name"], record["desired_state"])
    if case.get("configuration"):
        assert record["analyzed_configurations"] == {
            "status": "known", "value": [case["configuration"]]}, record
    assert_text_contract(case, ROOT)


def assert_fixer_idempotence(case):
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix=f"{case['name']}-", dir=build) as directory:
        temporary_root = Path(directory)
        shutil.copytree(CORPUS, temporary_root / "tests" / "fwd_order")

        first_text = assert_text_contract(case, temporary_root)
        first_fix = subprocess.run(
            [sys.executable, str(ROOT / "fix_includes.py"),
             "--nosafe_headers"],
            cwd=temporary_root, text=True, input=first_text,
            capture_output=True)
        assert first_fix.returncode == 0, (
            case["name"], first_fix.stdout, first_fix.stderr)
        compile_case(case, temporary_root)

        target = temporary_root / "tests" / "fwd_order" / case["target"]
        assert (case["decl"] in target.read_text()) == case["present"], (
            case["name"], target.read_text())

        before_second = tree_digest(temporary_root)
        second = analyze(case, temporary_root)
        assert second.returncode == 0, (case["name"], second.stderr)
        second_fix = subprocess.run(
            [sys.executable, str(ROOT / "fix_includes.py"),
             "--nosafe_headers"],
            cwd=temporary_root, text=True, input=second.stderr,
            capture_output=True)
        assert second_fix.returncode == 0, (
            case["name"], second_fix.stdout, second_fix.stderr)
        assert tree_digest(temporary_root) == before_second, case["name"]
        compile_case(case, temporary_root)

        second_json = analyze(case, temporary_root, json_output=True)
        assert second_json.returncode == 0, (
            case["name"], second_json.stderr)
        second_records = [
            json.loads(line) for line in second_json.stdout.splitlines()]
        assert not any(
            operation["disposition"] == "apply"
            for record in second_records
            for operation in record["operations"]), (
                case["name"], second_records)


def main():
    for case in CASES:
        assert_initial_contract(case)
        assert_fixer_idempotence(case)
    return 0


if __name__ == "__main__":
    sys.exit(main())
