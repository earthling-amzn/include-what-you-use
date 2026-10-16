#!/usr/bin/env python3
"""Opaque SEM-005 declaration-kind and named-database contracts."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

try:
    import jsonschema
except ImportError:
    print("SKIP: python jsonschema package is unavailable")
    sys.exit(77)


ROOT = Path(__file__).resolve().parents[1]
IWYU = Path(sys.argv[1]).resolve()
COMPILER = Path(sys.argv[2]).resolve()
DRIVER = ROOT / "iwyu_tool.py"
SCHEMA = json.loads((ROOT / "docs/output-v2.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
SOURCE = ROOT / "tests/sem005/variant_consumer.cc"
KINDS_SOURCE = ROOT / "tests/sem005/declaration_kinds.cc"


def run(command, **kwargs):
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                          **kwargs)


def parse_records(result):
    assert result.returncode == 0, result.stderr
    values = [json.loads(line) for line in result.stdout.splitlines()]
    assert values
    for value in values:
        VALIDATOR.validate(value)
    return values


def write_database(path, arguments):
    path.mkdir()
    entry = {
        "directory": str(ROOT),
        "file": str(SOURCE),
        "arguments": arguments,
    }
    (path / "compile_commands.json").write_text(json.dumps([entry]))


def driver_command(product_db, debug_db):
    return [
        sys.executable, str(DRIVER), "-o", "json", "--output-schema", "2",
        "--database", "product=" + str(product_db),
        "--database", "debug=" + str(debug_db),
    ]


def configuration(record):
    return record["invocation"]["configuration"]["value"]


def kind_for(record, qualified_name):
    return {fact["kind"] for fact in record["declaration_facts"]
            if fact["qualified_name"] == qualified_name}


def write_fake_analyzer(path):
    path.write_text(r'''#!/usr/bin/env python3
import json
import os
import sys

values = {}
args = sys.argv[1:]
for index, argument in enumerate(args[:-1]):
    if argument == "-Xiwyu" and args[index + 1].startswith("--"):
        option = args[index + 1][2:]
        if "=" in option:
            name, value = option.split("=", 1)
            values[name] = value
index = int(values["invocation_index"])
digest = values["command_digest"]
configuration = values["configuration"]
translation_unit = values["translation_unit"]
fact = {
    "qualified_name": "opaque::Entity",
    "kind": "class",
    "declaration": {"path": translation_unit, "line": 1, "column": 1},
}
conflicts = []
mode = os.environ.get("SEM005_MALFORMED")
if mode == "unknown_kind":
    fact["kind"] = "class-ish"
elif mode == "extra_fact_key":
    fact["spelling"] = "class Entity;"
elif mode == "forged_conflict":
    conflicts = [{"qualified_name": "opaque::Entity", "declarations": []}]
record = {
    "schema_version": 2,
    "record_type": "file_analysis",
    "invocation": {
        "id": "inv-%06d-%s" % (index, digest[7:19]),
        "index": index,
        "translation_unit": translation_unit,
        "configuration": {"status": "known", "value": configuration},
        "command_digest": digest,
    },
    "file": {"path": translation_unit, "kind": "source"},
    "status": "correct",
    "associated_headers": [],
    "analyzed_configurations": {"status": "known", "value": [configuration]},
    "operations": [],
    "desired_state": {"status": "known", "complete": True, "items": []},
    "diagnostics": [],
    "declaration_facts": [fact],
    "declaration_conflicts": conflicts,
}
print(json.dumps(record, separators=(",", ":")))
''')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def main():
    include_args = [str(COMPILER), "-I", str(ROOT), "-std=c++17"]
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        product_db = temporary / "product"
        debug_db = temporary / "debug"
        write_database(product_db, include_args + ["-c", str(SOURCE)])
        write_database(debug_db, include_args + [
            "-DSEM005_DEBUG_CONFIGURATION=1", "-c", str(SOURCE)])

        command = driver_command(product_db, debug_db)
        analyzer_env = os.environ.copy()
        analyzer_env["IWYU_BINARY"] = str(IWYU)
        first = run(command, env=analyzer_env)
        second = run(command, env=analyzer_env)
        assert first.stdout == second.stdout
        assert first.stderr == second.stderr
        records = parse_records(first)
        assert [configuration(record) for record in records] == [
            "product", "debug"]
        product, debug = records
        entity = "sem005_corpus::Handle"
        alias_entity = "sem005_corpus::AliasHandle"
        assert kind_for(product, entity) == {"typedef"}
        assert kind_for(debug, entity) == {"class"}
        assert kind_for(product, alias_entity) == {"using_alias"}
        assert kind_for(debug, alias_entity) == {"class"}
        expected_conflict = [
            {
                "qualified_name": alias_entity,
                "declarations": [
                    {"configuration": "debug", "kind": "class"},
                    {"configuration": "product", "kind": "using_alias"},
                ],
            },
            {
                "qualified_name": entity,
                "declarations": [
                    {"configuration": "debug", "kind": "class"},
                    {"configuration": "product", "kind": "typedef"},
                ],
            },
        ]
        assert product["declaration_conflicts"] == expected_conflict
        assert debug["declaration_conflicts"] == expected_conflict
        assert debug["status"] == "skipped"
        assert debug["desired_state"] == {
            "status": "unavailable", "complete": False, "items": None}
        assert debug["operations"]
        assert not any(operation["disposition"] == "apply"
                       for operation in debug["operations"])
        forward = [operation for operation in debug["operations"]
                   if operation["item_kind"] == "forward_declaration"]
        assert forward and all(operation["disposition"] == "report"
                               for operation in forward)
        assert any(diagnostic["code"] == "declaration_kind_conflict"
                   for diagnostic in debug["diagnostics"])

        # A single named configuration retains the native recommendation.
        single = run([
            sys.executable, str(DRIVER), "-o", "json", "--output-schema", "2",
            "--database", "debug=" + str(debug_db)], env=analyzer_env)
        single_record = parse_records(single)[0]
        assert single_record["declaration_conflicts"] == []
        assert any(operation["item_kind"] == "forward_declaration" and
                   operation["disposition"] == "apply"
                   for operation in single_record["operations"])

        # Schema v1 remains the direct-output default and has no v2 fields.
        legacy = run([str(IWYU), "-Xiwyu", "--output_format=json",
                      "-I", str(ROOT), "-std=c++17", str(SOURCE)])
        legacy_record = json.loads(legacy.stdout.splitlines()[0])
        assert legacy_record["schema_version"] == 1
        assert "declaration_facts" not in legacy_record
        assert "declaration_conflicts" not in legacy_record

        # Compile both real variants. The historically tempting debug-only
        # rewrite compiles in debug but conflicts with the product typedef.
        for define in ([], ["-DSEM005_DEBUG_CONFIGURATION=1"]):
            result = run(include_args + define + ["-c", str(SOURCE),
                         "-o", str(temporary / ("control%d.o" % len(define)))])
            assert result.returncode == 0, result.stderr
        unsafe = temporary / "unsafe.cc"
        unsafe.write_text(
            "namespace sem005_corpus { class Handle; }\n"
            "#include \"tests/sem005/variant_provider.hpp\"\n"
            "sem005_corpus::Handle* value;\n")
        debug_control = run(include_args + ["-DSEM005_DEBUG_CONFIGURATION=1",
                                            "-c", str(unsafe), "-o",
                                            str(temporary / "unsafe-debug.o")])
        assert debug_control.returncode == 0, debug_control.stderr
        product_control = run(include_args + ["-c", str(unsafe), "-o",
                                          str(temporary / "unsafe-product.o")])
        assert product_control.returncode != 0

        # The producer distinguishes every required declaration kind directly
        # from Clang declarations.
        kinds = run([str(IWYU), "-Xiwyu", "--output_format=json",
                     "-Xiwyu", "--output_schema=2", "-I", str(ROOT),
                     "-std=c++17", str(KINDS_SOURCE)])
        kinds_record = parse_records(kinds)[0]
        expected_kinds = {
            "sem005_kinds::ClassKind": "class",
            "sem005_kinds::StructKind": "struct",
            "sem005_kinds::UnionKind": "union",
            "sem005_kinds::ScopedEnumKind": "scoped_enum",
            "sem005_kinds::UnscopedEnumKind": "unscoped_enum",
            "sem005_kinds::ClassTemplateKind": "class_template",
            "sem005_kinds::TypedefKind": "typedef",
            "sem005_kinds::UsingAliasKind": "using_alias",
        }
        observed = {fact["qualified_name"]: fact["kind"]
                    for fact in kinds_record["declaration_facts"]
                    if fact["qualified_name"] in expected_kinds}
        assert observed == expected_kinds

        # Malformed and forged facts fail atomically before any JSON is emitted.
        fake = temporary / "fake-iwyu.py"
        write_fake_analyzer(fake)
        for mode, message in [
                ("unknown_kind", "invalid kind"),
                ("extra_fact_key", "malformed schema-v2 declaration fact"),
                ("forged_conflict", "must be an empty array")]:
            env = os.environ.copy()
            env["IWYU_BINARY"] = str(fake)
            env["SEM005_MALFORMED"] = mode
            malformed = run(command, env=env)
            assert malformed.returncode != 0
            assert malformed.stdout == ""
            assert message in malformed.stderr, malformed.stderr

    print("PASS: SEM-005 typed declaration conflicts and compile controls")


if __name__ == "__main__":
    main()
