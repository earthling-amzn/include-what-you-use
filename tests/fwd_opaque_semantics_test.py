#!/usr/bin/env python3

##===--- fwd_opaque_semantics_test.py - opaque declaration contracts ------===##
#
#                     The LLVM Compiler Infrastructure
#
# This file is distributed under the University of Illinois Open Source
# License. See LICENSE.TXT for details.
#
##===----------------------------------------------------------------------===##

"""Opaque contracts for friend and fixed-underlying-enum declarations."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
IWYU = Path(sys.argv[1]).resolve()


def run(source, json_output=False):
    command = [str(IWYU)]
    if json_output:
        command.extend(["-Xiwyu", "--output_format=json"])
    command.extend(["-I", ".", "-std=c++17", source])
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True)


def record(source):
    result = run(source, json_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 1, records
    return records[0]


def operations(value):
    return [
        (operation["subject_action"], operation["item_kind"],
         operation["spelling"], operation["disposition"],
         operation["reason_code"])
        for operation in value["operations"]
    ]


def compile_cxx17(source):
    cxx = os.environ.get("CXX", "c++")
    compiler = shutil.which(cxx)
    assert compiler, f"C++ compiler not found: {cxx}"
    result = subprocess.run(
        [compiler, "-std=c++17", "-fsyntax-only", "-I", str(ROOT),
         str(source)],
        cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def main():
    friend_first_path = "tests/native_json/fwd002_friend_first.cc"
    friend_provider_path = (
        "tests/native_json/fwd002_friend_provider_first.cc")
    enum_selected_path = "tests/native_json/fwd005_enum_selected.cc"
    enum_opaque_path = "tests/native_json/fwd005_enum_opaque.cc"

    friend_first = record(friend_first_path)
    assert operations(friend_first) == []
    assert friend_first["desired_state"]["items"] == [{
        "item_kind": "forward_declaration",
        "spelling": "class Task;",
        "symbols": [],
    }]

    friend_provider = record(friend_provider_path)
    assert operations(friend_provider) == [(
        "remove", "forward_declaration", "class Task;", "apply",
        "undesired_item_present")]
    assert friend_provider["desired_state"]["items"] == [{
        "item_kind": "include",
        "spelling": '#include "tests/native_json/fwd002_friend_provider.hpp"',
        "symbols": ["FriendProviderAnchor", "Task (ptr only)"],
    }]

    enum_selected = record(enum_selected_path)
    assert operations(enum_selected) == []
    assert enum_selected["desired_state"]["items"] == [{
        "item_kind": "include",
        "spelling": '#include "tests/native_json/fwd005_enum_provider.hpp"',
        "symbols": ["ForceProvider", "SelectedMode (ptr only)"],
    }]

    enum_opaque = record(enum_opaque_path)
    assert operations(enum_opaque) == [
        ("remove", "include",
         '#include "tests/native_json/fwd005_enum_provider.hpp"', "apply",
         "undesired_item_present"),
        ("add", "forward_declaration", "enum class SelectedMode : int;",
         "apply", "desired_item_missing"),
    ]
    assert enum_opaque["desired_state"]["items"] == [{
        "item_kind": "forward_declaration",
        "spelling": "enum class SelectedMode : int;",
        "symbols": [],
    }]

    text_friend_first = run(friend_first_path)
    assert text_friend_first.returncode == 0
    assert text_friend_first.stdout == ""
    assert ("(tests/native_json/fwd002_friend_first.cc has correct "
            "#includes/fwd-decls)") in text_friend_first.stderr

    text_friend_provider = run(friend_provider_path)
    assert text_friend_provider.returncode == 0
    assert "should add these lines:\n\n" in text_friend_provider.stderr
    assert ("should remove these lines:\n- class Task;  // lines 2-2\n"
            in text_friend_provider.stderr)
    assert ("The full include-list for " + friend_provider_path + ":\n"
            '#include "tests/native_json/fwd002_friend_provider.hpp"'
            in text_friend_provider.stderr)

    text_enum_selected = run(enum_selected_path)
    assert text_enum_selected.returncode == 0
    assert ("(tests/native_json/fwd005_enum_selected.cc has correct "
            "#includes/fwd-decls)") in text_enum_selected.stderr

    text_enum_opaque = run(enum_opaque_path)
    assert text_enum_opaque.returncode == 0
    assert ("should add these lines:\nenum class SelectedMode : int;\n"
            in text_enum_opaque.stderr)
    assert ("should remove these lines:\n"
            '- #include "tests/native_json/fwd005_enum_provider.hpp"  '
            "// lines 1-1\n" in text_enum_opaque.stderr)
    assert ("The full include-list for " + enum_opaque_path + ":\n"
            "enum class SelectedMode : int;\n" in text_enum_opaque.stderr)

    for source in (friend_first_path, friend_provider_path,
                   enum_selected_path, enum_opaque_path):
        compile_cxx17(ROOT / source)

    with tempfile.TemporaryDirectory() as directory:
        transformed = Path(directory)
        friend_provider_fixed = transformed / "friend_provider_fixed.cc"
        friend_provider_fixed.write_text(
            '#include "tests/native_json/fwd002_friend_provider.hpp"\n'
            "class Owner { friend class Task; };\n"
            "FriendProviderAnchor friend_provider_anchor;\n")
        enum_opaque_fixed = transformed / "enum_opaque_fixed.cc"
        enum_opaque_fixed.write_text(
            "enum class SelectedMode : int;\n"
            "SelectedMode transform_opaque(SelectedMode value);\n")
        compile_cxx17(friend_provider_fixed)
        compile_cxx17(enum_opaque_fixed)

    # Keep established enum redeclaration, opaque-enum, and template behavior
    # in this focused gate in addition to their regular CTest coverage.
    enum_redecl = run("tests/cxx/fwd_declare_enum.cc")
    assert enum_redecl.returncode == 0
    assert "- enum Bar;" in enum_redecl.stderr
    opaque_enums = run("tests/cxx/enums.cc")
    assert opaque_enums.returncode == 0
    assert "enum class DirectEnum1;" in opaque_enums.stderr
    assert '- #include "tests/cxx/enums-d1.h"' in opaque_enums.stderr
    template_args = run("tests/cxx/template_args.cc")
    assert template_args.returncode == 0, template_args.stderr

    return 0


if __name__ == "__main__":
    sys.exit(main())
