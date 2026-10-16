#!/usr/bin/env python3

##===--- macro_body_dependencies_test.py ----------------------------------===##
#
#                     The LLVM Compiler Infrastructure
#
# This file is distributed under the University of Illinois Open Source
# License. See LICENSE.TXT for details.
#
##===----------------------------------------------------------------------===##


"""End-to-end controls for native function-like macro dependencies."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def run(command, *, cwd, input_text=None):
    result = subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {command}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def macro_block(source):
    begin = source.index("#define MACRO_BODY_MAKE")
    end = source.index("\n\n#define MACRO_BODY_MEMBER", begin)
    return source[begin:end]


def write_sources(root, sources):
    for relative, content in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def run_reviewer_case(*, root, analyzer, compiler, fixer, name, sources,
                      retained=(), removed=(), provenance=None,
                      consumers=("future.cpp",)):
    case_root = root / "reviewer" / name
    case_root.mkdir(parents=True)
    write_sources(case_root, sources)

    analyzer_args = [
        analyzer, "-Xiwyu", "--verbose=3", "-Xiwyu",
        "--check_also=export.h", "-I", ".", "analysis.cc",
    ]
    text_run = run(analyzer_args, cwd=case_root)
    advice = text_run.stdout + text_run.stderr

    json_args = [
        analyzer, "-Xiwyu", "--output_format=json", "-Xiwyu",
        "--check_also=export.h", "-I", ".", "analysis.cc",
    ]
    json_run = run(json_args, cwd=case_root)
    records = [json.loads(line) for line in json_run.stdout.splitlines()]
    operations = [
        operation
        for record in records
        if record["file"]["path"].endswith("export.h")
        for operation in record["operations"]
    ]
    macro_operations = {
        operation["spelling"]: operation["reason_code"]
        for operation in operations
        if operation["reason_code"].startswith("macro_body_dependency")
    }
    for operation in operations:
        if operation["reason_code"] != "macro_body_dependency_unavailable":
            continue
        for field in ("selected", "candidates", "chain"):
            if operation["provider"][field]["status"] != "unavailable":
                raise AssertionError(
                    f"{name}: ambiguous {field} provenance was not unavailable"
                )
    if provenance is not None:
        for header, reason_code in provenance.items():
            matches = [
                code for spelling, code in macro_operations.items()
                if header in spelling
            ]
            if matches != [reason_code]:
                raise AssertionError(
                    f"{name}: expected {header} provenance {reason_code}, "
                    f"got {macro_operations}"
                )
        unexpected = [
            spelling for spelling in macro_operations
            if not any(header in spelling for header in provenance)
        ]
        if unexpected:
            raise AssertionError(
                f"{name}: unexpected macro provenance {unexpected}"
            )

    run([fixer, "--nosafe_headers", "--nocomments"], cwd=case_root,
        input_text=advice)
    export = (case_root / "export.h").read_text()
    for header in retained:
        if f'#include "{header}"' not in export:
            raise AssertionError(f"{name}: required provider removed: {header}")
    for header in removed:
        if f'#include "{header}"' in export:
            raise AssertionError(f"{name}: false provider retained: {header}")

    for index, consumer in enumerate(consumers):
        executable = case_root / f"future-{index}"
        run([compiler, "-std=c++17", "-I", ".", consumer,
             "-o", str(executable)], cwd=case_root)
        run([str(executable)], cwd=case_root)


def run_reviewer_reproducers(*, root, analyzer, compiler, fixer):
    cases = [
        {
            "name": "downstream-undef",
            "sources": {
                "provider.h": (
                    "#pragma once\nnamespace downstream_undef {\n"
                    "inline int select(int value) { return value + 1; }\n}\n"
                ),
                "export.h": (
                    "#pragma once\n#include \"provider.h\"\n"
                    "#define DOWNSTREAM_UNDEF(value) "
                    "::downstream_undef::select(value)\n"
                ),
                "analysis.cc": (
                    "#include \"export.h\"\n#undef DOWNSTREAM_UNDEF\n"
                    "int analysis_anchor;\n"
                ),
                "future.cpp": (
                    "#include \"export.h\"\nint main() { "
                    "return DOWNSTREAM_UNDEF(1) == 2 ? 0 : 1; }\n"
                ),
            },
            "retained": ("provider.h",),
            "provenance": {"provider.h": "macro_body_dependency"},
        },
        {
            "name": "downstream-redefine",
            "sources": {
                "provider.h": (
                    "#pragma once\nnamespace downstream_redefine {\n"
                    "inline int select(int value) { return value + 2; }\n}\n"
                ),
                "export.h": (
                    "#pragma once\n#include \"provider.h\"\n"
                    "#define DOWNSTREAM_REDEFINE(value) "
                    "::downstream_redefine::select(value)\n"
                ),
                "analysis.cc": (
                    "#include \"export.h\"\n#undef DOWNSTREAM_REDEFINE\n"
                    "#define DOWNSTREAM_REDEFINE(value) (value)\n"
                    "int analysis_anchor;\n"
                ),
                "future.cpp": (
                    "#include \"export.h\"\nint main() { "
                    "return DOWNSTREAM_REDEFINE(1) == 3 ? 0 : 1; }\n"
                ),
            },
            "retained": ("provider.h",),
            "provenance": {"provider.h": "macro_body_dependency"},
        },
        {
            "name": "namespace-qualified",
            "sources": {
                "a.h": (
                    "#pragma once\nnamespace qualified_a {\n"
                    "inline int select(int value) { return value + 3; }\n}\n"
                ),
                "b.h": (
                    "#pragma once\nnamespace qualified_b {\n"
                    "inline int select(int value) { return value + 30; }\n}\n"
                ),
                "export.h": (
                    "#pragma once\n#include \"a.h\"\n#include \"b.h\"\n"
                    "#define QUALIFIED_SELECT(value) "
                    "::qualified_a::select(value)\n"
                ),
                "analysis.cc": "#include \"export.h\"\nint analysis_anchor;\n",
                "future.cpp": (
                    "#include \"export.h\"\nint main() { "
                    "return QUALIFIED_SELECT(1) == 4 ? 0 : 1; }\n"
                ),
            },
            "retained": ("a.h",),
            "removed": ("b.h",),
            "provenance": {"a.h": "macro_body_dependency"},
        },
        {
            "name": "type-qualified-member",
            "sources": {
                "provider.h": (
                    "#pragma once\nstruct QualifiedType {\n"
                    "static int member(int value) { return value + 9; }\n};\n"
                ),
                "decoy.h": (
                    "#pragma once\nstruct OtherType {\n"
                    "static int member(int value) { return value + 90; }\n};\n"
                ),
                "export.h": (
                    "#pragma once\n#include \"provider.h\"\n#include \"decoy.h\"\n"
                    "#define TYPE_MEMBER(value) QualifiedType::member(value)\n"
                ),
                "analysis.cc": "#include \"export.h\"\nint analysis_anchor;\n",
                "future.cpp": (
                    "#include \"export.h\"\nint main() { "
                    "return TYPE_MEMBER(1) == 10 ? 0 : 1; }\n"
                ),
            },
            "retained": ("provider.h",),
            "removed": ("decoy.h",),
            "provenance": {"provider.h": "macro_body_dependency"},
        },
        {
            "name": "repeated-conditional-fileids",
            "sources": {
                "a.h": (
                    "#pragma once\nnamespace repeated_a {\n"
                    "inline int select(int value) { return value + 4; }\n}\n"
                ),
                "b.h": (
                    "#pragma once\nnamespace repeated_b {\n"
                    "inline int select(int value) { return value + 5; }\n}\n"
                ),
                "export.h": (
                    "#if REPEATED_MODE == 1\n#include \"a.h\"\n"
                    "#define REPEATED_SELECT(value) "
                    "::repeated_a::select(value)\n#else\n#include \"b.h\"\n"
                    "#define REPEATED_SELECT(value) "
                    "::repeated_b::select(value)\n#endif\n"
                ),
                "analysis.cc": (
                    "#define REPEATED_MODE 1\n#include \"export.h\"\n"
                    "#undef REPEATED_SELECT\n#undef REPEATED_MODE\n"
                    "#define REPEATED_MODE 2\n#include \"export.h\"\n"
                    "int analysis_anchor;\n"
                ),
                "future-a.cpp": (
                    "#define REPEATED_MODE 1\n#include \"export.h\"\n"
                    "int main() { return REPEATED_SELECT(1) == 5 ? 0 : 1; }\n"
                ),
                "future-b.cpp": (
                    "#define REPEATED_MODE 2\n#include \"export.h\"\n"
                    "int main() { return REPEATED_SELECT(1) == 6 ? 0 : 1; }\n"
                ),
            },
            "retained": ("a.h", "b.h"),
            "provenance": {
                "a.h": "macro_body_dependency",
                "b.h": "macro_body_dependency",
            },
            "consumers": ("future-a.cpp", "future-b.cpp"),
        },
        {
            "name": "using-declaration",
            "sources": {
                "impl.h": (
                    "#pragma once\nnamespace using_impl {\n"
                    "inline int select(int value) { return value + 6; }\n}\n"
                ),
                "wrapper.h": (
                    "#pragma once\n#include \"impl.h\"\nnamespace using_api {\n"
                    "using using_impl::select;\n}\n"
                ),
                "export.h": (
                    "#pragma once\n#include \"wrapper.h\"\n"
                    "#define USING_SELECT(value) ::using_api::select(value)\n"
                ),
                "analysis.cc": "#include \"export.h\"\nint analysis_anchor;\n",
                "future.cpp": (
                    "#include \"export.h\"\nint main() { "
                    "return USING_SELECT(1) == 7 ? 0 : 1; }\n"
                ),
            },
            "retained": ("wrapper.h",),
            "provenance": {"wrapper.h": "macro_body_dependency"},
        },
        {
            "name": "transitive-wrapper",
            "sources": {
                "provider.h": (
                    "#pragma once\nnamespace transitive_provider {\n"
                    "inline int select(int value) { return value + 7; }\n}\n"
                ),
                "wrapper.h": "#pragma once\n#include \"provider.h\"\n",
                "export.h": (
                    "#pragma once\n#include \"wrapper.h\"\n"
                    "#define TRANSITIVE_SELECT(value) "
                    "::transitive_provider::select(value)\n"
                ),
                "analysis.cc": "#include \"export.h\"\nint analysis_anchor;\n",
                "future.cpp": (
                    "#include \"export.h\"\nint main() { "
                    "return TRANSITIVE_SELECT(1) == 8 ? 0 : 1; }\n"
                ),
            },
            "retained": ("wrapper.h",),
            "provenance": {"wrapper.h": "macro_body_dependency"},
        },
        {
            "name": "generic-size",
            "sources": {
                "size-provider.h": (
                    "#pragma once\nstruct SizeProvider { "
                    "int size() const { return 99; } };\n"
                ),
                "export.h": (
                    "#pragma once\n#include \"size-provider.h\"\n"
                    "#define GENERIC_SIZE(value) ((value).size())\n"
                ),
                "analysis.cc": "#include \"export.h\"\nint analysis_anchor;\n",
                "future.cpp": (
                    "#include \"export.h\"\nstruct Local { "
                    "int size() const { return 8; } };\nint main() { "
                    "return GENERIC_SIZE(Local{}) == 8 ? 0 : 1; }\n"
                ),
            },
            "removed": ("size-provider.h",),
            "provenance": {},
        },
        {
            "name": "ambiguous-unqualified",
            "sources": {
                "a.h": (
                    "#pragma once\nnamespace ambiguous_a {\n"
                    "inline int choose(int value) { return value + 8; }\n}\n"
                ),
                "b.h": (
                    "#pragma once\nnamespace ambiguous_b {\n"
                    "inline int choose(int value) { return value + 80; }\n}\n"
                ),
                "export.h": (
                    "#pragma once\n#include \"a.h\"\n#include \"b.h\"\n"
                    "#define AMBIGUOUS_CHOOSE(value) choose(value)\n"
                ),
                "analysis.cc": "#include \"export.h\"\nint analysis_anchor;\n",
                "future.cpp": (
                    "#include \"export.h\"\nusing ambiguous_a::choose;\n"
                    "int main() { return AMBIGUOUS_CHOOSE(1) == 9 ? 0 : 1; }\n"
                ),
            },
            "retained": ("a.h", "b.h"),
            "provenance": {
                "a.h": "macro_body_dependency_unavailable",
                "b.h": "macro_body_dependency_unavailable",
            },
        },
        {
            "name": "same-file-undef",
            "sources": {
                "provider.h": "#pragma once\nstruct RetiredProvider {};\n",
                "export.h": (
                    "#pragma once\n#include \"provider.h\"\n"
                    "#define RETIRED_MACRO() RetiredProvider{}\n"
                    "#undef RETIRED_MACRO\n"
                ),
                "analysis.cc": "#include \"export.h\"\nint analysis_anchor;\n",
                "future.cpp": "#include \"export.h\"\nint main() { return 0; }\n",
            },
            "removed": ("provider.h",),
            "provenance": {},
        },
    ]

    for case in cases:
        run_reviewer_case(root=root, analyzer=analyzer, compiler=compiler,
                          fixer=fixer, **case)
    consumer_count = sum(len(case.get("consumers", ("future.cpp",)))
                         for case in cases)
    print(f"reviewer reproducers passed: {len(cases)} cases, "
          f"{consumer_count} future consumers")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analyzer", required=True)
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()

    source_root = Path(args.source).resolve()
    analyzer = str(Path(args.analyzer).resolve())
    compiler = str(Path(args.compiler).resolve())
    fixture_dir = source_root / "tests" / "cxx"

    with tempfile.TemporaryDirectory(prefix="iwyu-macro-body-") as temporary:
        root = Path(temporary)
        copied_dir = root / "tests" / "cxx"
        copied_dir.mkdir(parents=True)
        for fixture in fixture_dir.glob("macro_body_dependencies*"):
            shutil.copy2(fixture, copied_dir / fixture.name)

        translation_unit = "tests/cxx/macro_body_dependencies.cc"
        check_nested = [
            "-Xiwyu",
            "--check_also=tests/cxx/macro_body_dependencies-nested.h",
        ]
        text_run = run(
            [analyzer, "-Xiwyu", "--verbose=3", *check_nested,
             "-I", ".", translation_unit],
            cwd=root,
        )
        output = text_run.stdout + text_run.stderr
        for removed in ("ambiguous-a.h", "ambiguous-b.h", "disabled.h",
                        "member.h", "unrelated.h"):
            expected = f"macro_body_dependencies-{removed}"
            if expected not in output:
                raise AssertionError(f"missing removal evidence for {expected}")

        json_run = run(
            [analyzer, "-Xiwyu", "--output_format=json", *check_nested,
             "-I", ".", translation_unit],
            cwd=root,
        )
        records = [json.loads(line) for line in json_run.stdout.splitlines()]
        macro_keeps = {
            operation["spelling"]: operation["reason"]
            for record in records
            if record["file"]["path"].endswith(
                ("macro_body_dependencies.h",
                 "macro_body_dependencies-nested.h"))
            for operation in record["operations"]
            if operation["reason_code"] == "macro_body_dependency"
            and operation["disposition"] == "keep"
        }
        expected_tokens = {
            "macro_body_dependencies-provider.h": "::macro_body_dependencies::Payload",
            "macro_body_dependencies-free.h": "::macro_body_dependencies::transform",
            "macro_body_dependencies-conditional.h":
                "::macro_body_dependencies::conditional_transform",
            "macro_body_dependencies-nested-provider.h":
                "::macro_body_dependencies::nested_transform",
        }
        for header, token in expected_tokens.items():
            matching = [reason for spelling, reason in macro_keeps.items()
                        if header in spelling]
            if len(matching) != 1 or token not in matching[0]:
                raise AssertionError(
                    f"missing unambiguous macro provenance for {header}: "
                    f"{macro_keeps}"
                )
        negative_headers = ("ambiguous-a.h", "ambiguous-b.h", "disabled.h",
                            "member.h", "unrelated.h")
        if any(any(header in spelling for header in negative_headers)
               for spelling in macro_keeps):
            raise AssertionError(f"negative control was protected: {macro_keeps}")

        header = copied_dir / "macro_body_dependencies.h"
        before = header.read_text()
        run(
            [str(source_root / "fix_includes.py"), "--nosafe_headers",
             "--nocomments"],
            cwd=root,
            input_text=output,
        )
        after = header.read_text()
        if macro_block(after) != macro_block(before):
            raise AssertionError("continued macro changed next to include edits")
        preamble = after[:after.index("#define MACRO_BODY_MAKE")]
        main_retained = [
            header for header in expected_tokens
            if header != "macro_body_dependencies-nested-provider.h"
        ]
        main_retained.append("macro_body_dependencies-nested.h")
        for retained in main_retained:
            if retained not in preamble:
                raise AssertionError(f"required provider removed: {retained}")
        nested_after = (copied_dir / "macro_body_dependencies-nested.h").read_text()
        nested_preamble = nested_after[:nested_after.index("#define MACRO_BODY_INNER")]
        if "macro_body_dependencies-nested-provider.h" not in nested_preamble:
            raise AssertionError("nested macro declaration provider removed")
        for removed in ("macro_body_dependencies-ambiguous-a.h",
                        "macro_body_dependencies-ambiguous-b.h",
                        "macro_body_dependencies-disabled.h",
                        "macro_body_dependencies-member.h",
                        "macro_body_dependencies-unrelated.h"):
            if removed in preamble:
                raise AssertionError(f"unrelated provider retained: {removed}")

        executable = root / "future-consumer"
        run(
            [compiler, "-std=c++17", "-I", ".",
             "tests/cxx/macro_body_dependencies-future.cpp",
             "tests/cxx/macro_body_dependencies-provider.cpp",
             "-o", str(executable)],
            cwd=root,
        )
        run([str(executable)], cwd=root)

        run_reviewer_reproducers(
            root=root,
            analyzer=analyzer,
            compiler=compiler,
            fixer=str(source_root / "fix_includes.py"),
        )


if __name__ == "__main__":
    main()
