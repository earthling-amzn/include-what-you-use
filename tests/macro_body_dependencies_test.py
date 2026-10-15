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
                        "unrelated.h"):
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
            "macro_body_dependencies-provider.h": "Payload",
            "macro_body_dependencies-member.h": "member",
            "macro_body_dependencies-free.h": "transform",
            "macro_body_dependencies-conditional.h": "conditional_transform",
            "macro_body_dependencies-nested-provider.h": "nested_transform",
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
                            "unrelated.h")
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


if __name__ == "__main__":
    main()
