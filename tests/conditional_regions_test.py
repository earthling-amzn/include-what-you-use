#!/usr/bin/env python3
"""Opaque MC-002 conditional-region census contract."""

import json
import hashlib
from pathlib import Path
import subprocess
import sys

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
IWYU = Path(sys.argv[1]).resolve()
CXX = sys.argv[2]
SCHEMA = json.loads((ROOT / "docs/output-v2.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
SOURCE = "tests/conditional_regions/conditional.cc"
SCOPE_SOURCE = "tests/conditional_regions/scope.cc"

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


def analyze(configuration, flags, source=SOURCE):
    command = [str(IWYU), "-Xiwyu", "--output_format=json", "-Xiwyu",
               "--configuration=" + configuration, "-I", ".", "-std=c++17",
               *flags, source]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    for record in records:
        VALIDATOR.validate(record)
    return next(record for record in records
                if record["file"]["path"].endswith(source.rsplit("/", 1)[-1]))


def main():
    source_bytes = (ROOT / SOURCE).read_bytes()
    records = {name: analyze(name, flags) for name, flags in CONFIGS.items()}
    identities = None
    for name, record in records.items():
        regions = record["conditional_regions"]
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

    # Clang's final macro-expanded token stream, rather than a raw brace scan,
    # proves legal include scope. Comments, normal strings, and raw strings do
    # not create scope; function/class/namespace braces and macro-expanded
    # namespace braces do.
    scope_flags = ["-DFUNCTION_REGION", "-DCLASS_REGION",
                   "-DNAMESPACE_REGION", "-DMACRO_REGION", "-DFILE_REGION"]
    scope = analyze("scope", scope_flags, SCOPE_SOURCE)
    scope_regions = scope["conditional_regions"]
    assert [region["lexical_scope"] for region in scope_regions] == [
        "non_file", "non_file", "non_file", "non_file", "file"]
    scope_compile = subprocess.run(
        [CXX, "-std=c++17", "-I", str(ROOT), *scope_flags,
         "-fsyntax-only", str(ROOT / SCOPE_SOURCE)],
        text=True, capture_output=True)
    assert scope_compile.returncode == 0, scope_compile.stderr
    print("PASS: MC-002 complete conditional-region census and scope facts")


if __name__ == "__main__":
    main()
