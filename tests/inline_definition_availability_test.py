#!/usr/bin/env python3
"""Opaque SEM-004 inline-definition availability contracts."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "inline_definition_availability"
IWYU = Path(sys.argv[1]).resolve()
CXX = Path(sys.argv[2]).resolve()


def run(command, cwd=CORPUS, stdin=None, expect=0):
    result = subprocess.run([str(arg) for arg in command], cwd=cwd,
                            input=stdin, text=True, capture_output=True)
    assert result.returncode == expect, (
        f"command returned {result.returncode}, expected {expect}: {command}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def analyze(filename, corpus=CORPUS):
    command = [IWYU, "-Xiwyu", "--output_format=json", "-I", ".",
               "-std=c++17", filename]
    first = run(command, cwd=corpus)
    second = run(command, cwd=corpus)
    assert second.stdout == first.stdout, filename
    records = [json.loads(line) for line in first.stdout.splitlines()]
    return next(record for record in records
                if record["file"]["path"] == filename)


def assert_analyzer_contracts():
    available = analyze("available.cc")
    assert available["status"] == "correct"
    assert available["operations"] == []
    assert available["diagnostics"] == []
    assert available["desired_state"]["items"] == [{
        "item_kind": "include",
        "spelling": '#include "api.inline.hpp"',
        "symbols": [
            "InlineFree", "InlineLifecycle",
            "InlineLifecycle::InlineLifecycle", "InlineLifecycle::Member",
            "InlineLifecycle::~InlineLifecycle",
        ],
    }]

    missing = analyze("missing.cc")
    assert missing["status"] == "correct"
    assert missing["operations"] == []
    facts = [diagnostic for diagnostic in missing["diagnostics"]
             if diagnostic["code"] == "missing_inline_definition"]
    assert [(fact["message"], fact["location"]["value"]) for fact in facts] == [
        ("Inline function 'InlineLifecycle::InlineLifecycle' is odr-used, "
         "but no definition is visible; include its inline-definition "
         "provider before linking.",
         {"column": 19, "line": 4, "path": "missing.cc"}),
        ("Inline function 'InlineLifecycle::~InlineLifecycle' is odr-used, "
         "but no definition is visible; include its inline-definition "
         "provider before linking.",
         {"column": 19, "line": 4, "path": "missing.cc"}),
        ("Inline function 'InlineLifecycle::Member' is odr-used, but no "
         "definition is visible; include its inline-definition provider "
         "before linking.",
         {"column": 16, "line": 5, "path": "missing.cc"}),
        ("Inline function 'InlineFree' is odr-used, but no definition is "
         "visible; include its inline-definition provider before linking.",
         {"column": 27, "line": 5, "path": "missing.cc"}),
    ]

    control = analyze("control.cc")
    assert not [diagnostic for diagnostic in control["diagnostics"]
                if diagnostic["code"] == "missing_inline_definition"]
    assert [(op["subject_action"], op["disposition"], op["spelling"])
            for op in control["operations"]] == [
        ("remove", "apply", '#include "unused.inline.hpp"')]


def assert_gcc_backstop(corpus=CORPUS):
    with tempfile.TemporaryDirectory(prefix="sem004-gcc-", dir=ROOT / "build") as tmp:
        out = Path(tmp)
        available = out / "available"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "available.cc",
             "-o", available], cwd=corpus)
        run([available], cwd=corpus)

        control = out / "control"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "control.cc",
             "noninline.cc", "-o", control], cwd=corpus)
        run([control], cwd=corpus)

        missing_object = out / "missing.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "missing.cc", "-o", missing_object], cwd=corpus)
        link = subprocess.run([str(CXX), str(missing_object), "-o",
                               str(out / "missing")], cwd=corpus,
                              text=True, capture_output=True)
        assert link.returncode != 0, link.stdout + link.stderr
        linker_output = link.stdout + link.stderr
        for symbol in ("InlineLifecycle::InlineLifecycle()",
                       "InlineLifecycle::Member() const", "InlineFree(int)",
                       "InlineLifecycle::~InlineLifecycle()"):
            assert symbol in linker_output, linker_output


def assert_fixer_idempotence():
    with tempfile.TemporaryDirectory(prefix="sem004-fix-", dir=ROOT / "build") as tmp:
        corpus = Path(tmp) / "inline_definition_availability"
        shutil.copytree(CORPUS, corpus)
        for filename in ("available.cc", "control.cc"):
            first = run([IWYU, "-I", ".", "-std=c++17", filename], cwd=corpus)
            run([sys.executable, ROOT / "fix_includes.py", "--nosafe_headers",
                 "--nocomments", "--noblank_lines"], cwd=corpus,
                stdin=first.stderr)
            second = run([IWYU, "-I", ".", "-std=c++17", filename], cwd=corpus)
            assert "should add these lines:" not in second.stderr, second.stderr
            assert "should remove these lines:" not in second.stderr, second.stderr
        assert '#include "api.inline.hpp"' in (
            corpus / "available.cc").read_text()
        assert "unused.inline.hpp" not in (corpus / "control.cc").read_text()
        assert_gcc_backstop(corpus)


if __name__ == "__main__":
    assert_analyzer_contracts()
    assert_gcc_backstop()
    assert_fixer_idempotence()
    print("SEM-004 inline-definition availability contracts passed")
