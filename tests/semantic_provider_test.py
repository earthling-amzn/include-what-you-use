#!/usr/bin/env python3
"""Opaque SEM-003 provider-evidence and bounded-association contracts."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "semantic_providers"
IWYU = Path(sys.argv[1]).resolve()
CXX = Path(sys.argv[2]).resolve()


def run(command, cwd=CORPUS, stdin=None, expect=0):
    result = subprocess.run([str(arg) for arg in command], cwd=cwd,
                            input=stdin, text=True, capture_output=True)
    assert result.returncode == expect, (
        f"command returned {result.returncode}, expected {expect}: {command}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def analyze(filename, corpus=CORPUS, extra=()):
    result = run([IWYU, "-Xiwyu", "--output_format=json", *extra,
                  "-I", ".", "-std=c++17", filename], cwd=corpus)
    values = [json.loads(line) for line in result.stdout.splitlines()]
    first = next(value for value in values
                 if value["file"]["path"] == filename)
    repeat = run([IWYU, "-Xiwyu", "--output_format=json", *extra,
                  "-I", ".", "-std=c++17", filename], cwd=corpus)
    assert repeat.stdout == result.stdout, filename
    return first


def add_operation(record, spelling):
    return next(op for op in record["operations"]
                if op["subject_action"] == "add" and
                op["spelling"] == f'#include "{spelling}"')


def provider_ref(corpus, filename):
    return {
        "path": str((corpus / filename).resolve()).replace("\\", "/"),
        "spelling": f'"{filename}"',
    }


def assert_symbol_evidence(operation, filename, symbol, declaration):
    assert len(operation["symbols"]) == 1, operation
    fact = operation["symbols"][0]
    assert fact["name"] == symbol
    assert fact["qualified_name"] == {"status": "known", "value": symbol}
    assert fact["declaration"] == {
        "status": "known", "value": declaration}
    assert fact["uses"], fact
    assert all(use["location"]["path"] == filename and
               use["requirement"] == "full"
               for use in fact["uses"]), fact


def expected_provider(corpus, analyzed, selected, chain):
    ref = provider_ref(corpus, selected)
    return {
        "selected": {"status": "known", "value": ref},
        "candidates": {"status": "known", "value": [ref]},
        "chain": {
            "status": "known",
            "value": [analyzed] + [
                str((corpus / path).resolve()).replace("\\", "/")
                for path in chain
            ],
        },
    }


def unavailable_provider():
    return {
        "selected": {"status": "unavailable", "value": None},
        "candidates": {"status": "unavailable", "value": None},
        "chain": {"status": "unavailable", "value": None},
    }


def assert_contracts():
    direct = analyze("direct.cc")
    assert direct["status"] == "correct"
    assert direct["operations"] == []
    assert direct["diagnostics"] == []

    transitive = analyze("transitive.cc")
    transitive_add = add_operation(transitive, "provider.hpp")
    assert transitive_add["provider"] == expected_provider(
        CORPUS, "transitive.cc", "provider.hpp",
        ["wrapper.hpp", "provider.hpp"])
    assert_symbol_evidence(
        transitive_add, "transitive.cc", "SemanticProvider",
        {"column": 8, "line": 4, "path": "provider.hpp"})
    assert [diag["code"] for diag in transitive["diagnostics"]] == [
        "missing_direct_include", "missing_direct_include"]

    associated = analyze("associated.cc")
    assert associated["status"] == "correct"
    assert associated["diagnostics"] == []
    assert associated["associated_headers"] == [{
        "path": "associated.hpp",
        "relationship": "declarations",
        "reason_code": "canonical_stem",
        "reason": "Declaration header matches the translation-unit stem.",
    }]
    associated_add = add_operation(associated, "provider.hpp")
    assert (associated_add["disposition"], associated_add["reason_code"]) == (
        "suppress", "provided_by_associated_header")
    assert associated_add["provider"] == expected_provider(
        CORPUS, "associated.cc", "provider.hpp",
        ["associated.hpp", "provider.hpp"])
    assert_symbol_evidence(
        associated_add, "associated.cc", "SemanticProvider",
        {"column": 8, "line": 4, "path": "provider.hpp"})

    capped = analyze("capped.cc")
    capped_add = add_operation(capped, "provider.hpp")
    assert capped_add["disposition"] == "apply"
    assert capped_add["provider"] == expected_provider(
        CORPUS, "capped.cc", "provider.hpp",
        ["capped.hpp", "wrapper.hpp", "provider.hpp"])
    assert [diag["code"] for diag in capped["diagnostics"]] == [
        "missing_direct_include", "missing_direct_include"]

    absent_args = ("-Xiwyu", "--prefix_header_includes=remove",
                   "-include", "provider.hpp")
    absent = analyze("absent.cc", extra=absent_args)
    absent_add = add_operation(absent, "provider.hpp")
    ref = provider_ref(CORPUS, "provider.hpp")
    assert absent_add["provider"] == {
        "selected": {"status": "known", "value": ref},
        "candidates": {"status": "known", "value": [ref]},
        "chain": {"status": "unavailable", "value": None},
    }
    assert (absent_add["disposition"], absent_add["reason_code"]) == (
        "suppress", "prefix_header_add_suppressed")
    assert_symbol_evidence(
        absent_add, "absent.cc", "SemanticProvider",
        {"column": 8, "line": 4, "path": "provider.hpp"})
    assert [diag["code"] for diag in absent["diagnostics"]] == [
        "missing_direct_include", "missing_direct_include"]

    mapped_args = ("-Xiwyu", "--mapping_file=providers.imp")
    mapped = analyze("mapped.cc", extra=mapped_args)
    mapped_add = add_operation(mapped, "public.hpp")
    assert mapped_add["provider"] == expected_provider(
        CORPUS, "mapped.cc", "public.hpp",
        ["mapped_anchor.hpp", "public.hpp"])
    assert_symbol_evidence(
        mapped_add, "mapped.cc", "MappedSemanticProvider",
        {"column": 8, "line": 4, "path": "private.hpp"})
    assert [diag["code"] for diag in mapped["diagnostics"]] == [
        "missing_direct_include", "missing_direct_include"]

    repeated = analyze("repeated.cc")
    repeated_add = add_operation(repeated, "repeated_private.hpp")
    assert repeated_add["provider"] == expected_provider(
        CORPUS, "repeated.cc", "repeated_private.hpp",
        ["repeated_second.hpp", "repeated_wrapper.hpp",
         "repeated_private.hpp"])
    repeated_chain = repeated_add["provider"]["chain"]["value"]
    assert not any(path.endswith("/repeated_first.hpp")
                   for path in repeated_chain), repeated_chain

    relative_corpus = CORPUS / "relative"
    relative_args = ("-Xiwyu", "--mapping_file=../relative.imp")
    relative = analyze("relative.cc", corpus=relative_corpus,
                       extra=relative_args)
    relative_add = add_operation(relative, "../public/relative_public.hpp")
    assert relative_add["provider"] == expected_provider(
        relative_corpus, "relative.cc", "../public/relative_public.hpp",
        ["relative_anchor.hpp", "../public/relative_public.hpp"])
    assert_symbol_evidence(
        relative_add, "relative.cc", "RelativeSemanticProvider",
        {"column": 8, "line": 4, "path": "relative_private.hpp"})

    unseen_args = ("-Xiwyu", "--mapping_file=unseen.imp")
    unseen = analyze("unseen.cc", extra=unseen_args)
    unseen_add = add_operation(unseen, "unseen_public.hpp")

    contextual_corpus = CORPUS / "contextual"
    contextual_args = ("-Xiwyu", "--mapping_file=../contextual.imp")
    contextual = analyze("contextual.cc", corpus=contextual_corpus,
                         extra=contextual_args)
    contextual_add = add_operation(contextual, "public.hpp")
    assert contextual_add["provider"] == expected_provider(
        contextual_corpus, "contextual.cc", "public.hpp",
        ["local_anchor.hpp", "public.hpp"])
    contextual_path = contextual_add["provider"]["selected"]["value"]["path"]
    assert "/remote/" not in contextual_path, contextual_path
    assert unseen_add["provider"] == unavailable_provider()


def compile_sources(corpus):
    for filename in ("direct.cc", "transitive.cc", "associated.cc",
                     "capped.cc", "mapped.cc", "repeated.cc", "unseen.cc"):
        run([CXX, "-std=c++17", "-fsyntax-only", "-I", ".", filename],
            cwd=corpus)
    run([CXX, "-std=c++17", "-fsyntax-only", "-include", "provider.hpp",
         "-I", ".", "absent.cc"], cwd=corpus)
    run([CXX, "-std=c++17", "-fsyntax-only", "-I", ".", "relative.cc"],
        cwd=corpus / "relative")
    run([CXX, "-std=c++17", "-fsyntax-only", "-I", ".", "contextual.cc"],
        cwd=corpus / "contextual")


def assert_fixer_idempotence():
    with tempfile.TemporaryDirectory(prefix="sem003-", dir=ROOT / "build") as tmp:
        corpus = Path(tmp) / "semantic_providers"
        shutil.copytree(CORPUS, corpus)
        cases = [
            (corpus, "transitive.cc", ()),
            (corpus, "mapped.cc",
             ("-Xiwyu", "--mapping_file=providers.imp")),
            (corpus, "repeated.cc", ()),
            (corpus / "relative", "relative.cc",
             ("-Xiwyu", "--mapping_file=../relative.imp")),
            (corpus / "contextual", "contextual.cc",
             ("-Xiwyu", "--mapping_file=../contextual.imp")),
        ]
        for cwd, filename, extra in cases:
            first = run([IWYU, *extra, "-I", ".", "-std=c++17", filename],
                        cwd=cwd)
            run([sys.executable, ROOT / "fix_includes.py", "--nosafe_headers",
                 "--nocomments", "--noblank_lines"], cwd=cwd,
                stdin=first.stderr)
            second = run([IWYU, *extra, "-I", ".", "-std=c++17", filename],
                         cwd=cwd)
            assert "should add these lines:" not in second.stderr, second.stderr
            assert "should remove these lines:" not in second.stderr, second.stderr
            run([CXX, "-std=c++17", "-fsyntax-only", "-I", ".", filename],
                cwd=cwd)


if __name__ == "__main__":
    assert_contracts()
    compile_sources(CORPUS)
    assert_fixer_idempotence()
    print("SEM-003 provider evidence contracts passed")
