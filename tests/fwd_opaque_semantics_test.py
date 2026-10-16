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


def run(source, json_output=False, extra_args=()):
    command = [str(IWYU)]
    if json_output:
        command.extend(["-Xiwyu", "--output_format=json"])
    command.extend(["-I", ".", "-std=c++17"])
    command.extend(extra_args)
    command.append(source)
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True)


def record(source, extra_args=()):
    result = run(source, json_output=True, extra_args=extra_args)
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


def cxx17_result(source, extra_args=()):
    cxx = os.environ.get("CXX", "c++")
    compiler = shutil.which(cxx)
    assert compiler, f"C++ compiler not found: {cxx}"
    return subprocess.run(
        [compiler, "-std=c++17", "-fsyntax-only", "-I", str(ROOT),
         *extra_args, str(source)],
        cwd=ROOT, text=True, capture_output=True)


def compile_cxx17(source, extra_args=()):
    result = cxx17_result(source, extra_args)
    assert result.returncode == 0, result.stdout + result.stderr


def reject_cxx17(source):
    result = cxx17_result(source)
    assert result.returncode != 0, "ordinary lookup unexpectedly succeeded"


def main():
    friend_first_path = "tests/native_json/fwd002_friend_first.cc"
    friend_provider_path = (
        "tests/native_json/fwd002_friend_provider_first.cc")
    template_friend_paths = {
        "unqualified": (
            "tests/native_json/fwd002_template_friend_unqualified.cc"),
        "namespace": (
            "tests/native_json/fwd002_template_friend_namespace.cc"),
        "global": "tests/native_json/fwd002_template_friend_global.cc",
        "conditional": (
            "tests/native_json/fwd002_template_friend_conditional.cc"),
        "future": (
            "tests/native_json/fwd002_template_friend_future_lookup.cc"),
        "provider_selected": (
            "tests/native_json/fwd002_template_friend_provider_selected.cc"),
        "provider_removed": (
            "tests/native_json/fwd002_template_friend_provider_removed.cc"),
    }
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

    opaque_template_declarations = {
        "unqualified": "template <typename T> class TemplateTask;",
        "namespace": "template <typename T> class NamespaceTemplateTask;",
        "global": "template <typename T> class GlobalTemplateTask;",
        "future": "template <typename T> class FutureTemplateTask;",
    }
    for case, declaration in opaque_template_declarations.items():
        template_friend = record(template_friend_paths[case])
        assert operations(template_friend) == []
        assert template_friend["desired_state"]["items"] == [{
            "item_kind": "forward_declaration",
            "spelling": declaration,
            "symbols": [],
        }]

    conditional_template_friend = record(
        template_friend_paths["conditional"],
        extra_args=("-DENABLE_TEMPLATE_FRIEND=1",))
    assert operations(conditional_template_friend) == []
    assert conditional_template_friend["desired_state"]["items"] == [{
        "item_kind": "forward_declaration",
        "spelling": (
            "template <typename T> class ConditionalTemplateTask;"),
        "symbols": [],
    }]

    template_provider_selected = record(
        template_friend_paths["provider_selected"])
    assert operations(template_provider_selected) == [(
        "remove", "forward_declaration",
        "template <typename T> class ProvidedTemplateTask;", "apply",
        "undesired_item_present")]
    assert template_provider_selected["desired_state"]["items"] == [{
        "item_kind": "include",
        "spelling": (
            '#include "tests/native_json/'
            'fwd002_template_friend_provider.hpp"'),
        "symbols": [
            "ProvidedTemplateTask (ptr only)",
            "TemplateFriendProviderAnchor",
        ],
    }]

    template_provider_removed = record(
        template_friend_paths["provider_removed"])
    assert operations(template_provider_removed) == [(
        "remove", "include",
        '#include "tests/native_json/fwd002_template_friend_provider.hpp"',
        "apply", "undesired_item_present")]
    assert template_provider_removed["desired_state"]["items"] == [{
        "item_kind": "forward_declaration",
        "spelling": "template <typename T> class ProvidedTemplateTask;",
        "symbols": [],
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

    for case in ("unqualified", "namespace", "global", "future"):
        template_text = run(template_friend_paths[case])
        assert template_text.returncode == 0, template_text.stderr
        assert template_text.stdout == ""
        assert ("(" + template_friend_paths[case] +
                " has correct #includes/fwd-decls)" in template_text.stderr)

    conditional_template_text = run(
        template_friend_paths["conditional"],
        extra_args=("-DENABLE_TEMPLATE_FRIEND=1",))
    assert conditional_template_text.returncode == 0
    assert ("(" + template_friend_paths["conditional"] +
            " has correct #includes/fwd-decls)" in
            conditional_template_text.stderr)

    selected_template_text = run(
        template_friend_paths["provider_selected"])
    assert selected_template_text.returncode == 0
    assert ("- template <typename T> class ProvidedTemplateTask;"
            in selected_template_text.stderr)
    assert ('#include "tests/native_json/'
            'fwd002_template_friend_provider.hpp"' in
            selected_template_text.stderr)

    removed_template_text = run(template_friend_paths["provider_removed"])
    assert removed_template_text.returncode == 0
    assert ('- #include "tests/native_json/'
            'fwd002_template_friend_provider.hpp"' in
            removed_template_text.stderr)
    assert ("template <typename T> class ProvidedTemplateTask;" in
            removed_template_text.stderr)

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
                   enum_selected_path, enum_opaque_path,
                   *template_friend_paths.values()):
        compile_cxx17(ROOT / source)
    compile_cxx17(
        ROOT / template_friend_paths["conditional"],
        extra_args=("-DENABLE_TEMPLATE_FRIEND=1",))

    with tempfile.TemporaryDirectory() as directory:
        transformed = Path(directory)
        friend_provider_fixed = transformed / "friend_provider_fixed.cc"
        friend_provider_fixed.write_text(
            '#include "tests/native_json/fwd002_friend_provider.hpp"\n'
            "class Owner { friend class Task; };\n"
            "FriendProviderAnchor friend_provider_anchor;\n")
        template_provider_selected_fixed = (
            transformed / "template_provider_selected_fixed.cc")
        template_provider_selected_fixed.write_text(
            '#include "tests/native_json/'
            'fwd002_template_friend_provider.hpp"\n'
            "class SelectedTemplateOwner {\n"
            "  template <typename T> friend class ProvidedTemplateTask;\n"
            "};\n"
            "TemplateFriendProviderAnchor template_friend_provider_anchor;\n")
        template_provider_removed_fixed = (
            transformed / "template_provider_removed_fixed.cc")
        template_provider_removed_fixed.write_text(
            "template <typename T> class ProvidedTemplateTask;\n"
            "class RemovedTemplateOwner {\n"
            "  template <typename T> friend class ProvidedTemplateTask;\n"
            "};\n")
        future_without_declaration = (
            transformed / "future_without_declaration.cc")
        future_lines = (
            ROOT / template_friend_paths["future"]).read_text().splitlines()
        future_without_declaration.write_text(
            "\n".join(future_lines[1:]) + "\n")
        enum_opaque_fixed = transformed / "enum_opaque_fixed.cc"
        enum_opaque_fixed.write_text(
            "enum class SelectedMode : int;\n"
            "SelectedMode transform_opaque(SelectedMode value);\n")
        compile_cxx17(friend_provider_fixed)
        compile_cxx17(template_provider_selected_fixed)
        compile_cxx17(template_provider_removed_fixed)
        reject_cxx17(future_without_declaration)
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
