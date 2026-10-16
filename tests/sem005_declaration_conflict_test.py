#!/usr/bin/env python3
"""Opaque SEM-005 declaration-kind and named-database contracts."""

import hashlib
import json
import os
from pathlib import Path
import shutil
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
    cwd = kwargs.pop("cwd", ROOT)
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True,
                          **kwargs)


def parse_records(result):
    assert result.returncode == 0, result.stderr
    values = [json.loads(line) for line in result.stdout.splitlines()]
    assert values
    for value in values:
        VALIDATOR.validate(value)
    return values


def write_database(path, arguments, source=SOURCE, directory=ROOT):
    path.mkdir()
    entry = {
        "directory": str(directory),
        "file": str(source),
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


def fixer_report(record, target):
    additions = [
        operation["spelling"] for operation in record["operations"]
        if operation["subject_action"] == "add" and
           operation["disposition"] == "apply"]
    full_list = [item["spelling"]
                 for item in record["desired_state"]["items"]]
    filename = str(target)
    return "\n".join([
        filename + " should add these lines:",
        *additions,
        "",
        filename + " should remove these lines:",
        "",
        "The full include-list for " + filename + ":",
        *full_list,
        "---",
        "",
    ])


def kind_for(record, qualified_name):
    return {fact["kind"] for fact in record["declaration_facts"]
            if fact["qualified_name"] == qualified_name}


def write_fake_analyzer(path):
    path.write_text(r'''#!/usr/bin/env python3
import hashlib
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
with open(translation_unit, "rb") as source_file:
    source_digest = "sha256:" + hashlib.sha256(source_file.read()).hexdigest()
source_identity = os.path.realpath(translation_unit).replace(os.sep, "/")
file_path = translation_unit
mode = os.environ.get("SEM005_MALFORMED")
if mode == "different_digest" and configuration == "debug":
    source_digest = "sha256:" + "f" * 64
elif mode == "identity_forgery":
    source_identity = os.path.realpath(translation_unit + ".forged").replace(os.sep, "/")
elif mode == "path_forgery":
    file_path = os.path.realpath(__file__).replace(os.sep, "/")
fact = {
    "qualified_name": "opaque::Entity",
    "kind": "class",
    "declaration": {"path": translation_unit, "line": 1, "column": 1},
}
conflicts = []
if mode == "source_race" and configuration == "product":
    with open(translation_unit, "ab") as source_file:
        source_file.write(b"// raced after producer digest\n")
if mode == "unknown_kind":
    fact["kind"] = "class-ish"
elif mode == "extra_fact_key":
    fact["spelling"] = "class Entity;"
elif mode == "forged_conflict":
    conflicts = [{
        "qualified_name": "opaque::Entity",
        "source_digest": source_digest,
        "declarations": [
            {"configuration": "debug", "kind": "class"},
            {"configuration": "product", "kind": "union"},
        ],
    }]
facts = [fact]
if mode == "kind_matrix":
    product_kinds = {
        "matrix::AliasConflict": "typedef",
        "matrix::CompatibleRecord": "class",
        "matrix::EnumKindConflict": "scoped_enum",
        "matrix::RecordUnionConflict": "class",
        "matrix::TemplateConflict": "class_template",
    }
    debug_kinds = {
        "matrix::AliasConflict": "using_alias",
        "matrix::CompatibleRecord": "struct",
        "matrix::EnumKindConflict": "unscoped_enum",
        "matrix::RecordUnionConflict": "union",
        "matrix::TemplateConflict": "class",
    }
    selected = product_kinds if configuration == "product" else debug_kinds
    facts = [{
        "qualified_name": name,
        "kind": kind,
        "declaration": {"path": translation_unit, "line": 1, "column": 1},
    } for name, kind in sorted(selected.items())]
record = {
    "schema_version": 2,
    "record_type": "file_analysis",
    "source_identity": source_identity,
    "source_digest": source_digest,
    "tool": {
        "name": "include-what-you-use",
        "version": "fake-1",
        "git_revision": {"status": "unavailable", "value": None},
        "clang_version": "fake-clang-17",
    },
    "invocation": {
        "id": "inv-%06d-%s" % (index, digest[7:19]),
        "index": index,
        "translation_unit": translation_unit,
        "configuration": {"status": "known", "value": configuration},
        "command_digest": digest,
    },
    "file": {"path": file_path, "kind": "source"},
    "status": "correct",
    "associated_headers": [],
    "analyzed_configurations": {"status": "known", "value": [configuration]},
    "operations": [],
    "desired_state": {"status": "known", "complete": True, "items": []},
    "diagnostics": [],
    "declaration_facts": facts,
    "declaration_conflicts": conflicts,
}
if mode == "missing_tool":
    del record["tool"]
elif mode == "missing_operations":
    del record["operations"]
elif mode == "semantic_status":
    record["status"] = "changes"
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
        enum_entity = "sem005_corpus::EnumClassHandle"
        compatible_entity = "sem005_corpus::CompatibleRecord"
        source_digest = "sha256:" + hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        source_identity = str(SOURCE.resolve()).replace(os.sep, "/")
        assert product["source_identity"] == source_identity
        assert debug["source_identity"] == source_identity
        assert product["source_digest"] == source_digest
        assert debug["source_digest"] == source_digest
        assert kind_for(product, entity) == {"typedef"}
        assert kind_for(debug, entity) == {"class"}
        assert kind_for(product, alias_entity) == {"using_alias"}
        assert kind_for(debug, alias_entity) == {"class"}
        assert kind_for(product, enum_entity) == {"class"}
        assert kind_for(debug, enum_entity) == {"scoped_enum"}
        assert kind_for(product, compatible_entity) == {"struct"}
        assert kind_for(debug, compatible_entity) == {"class"}
        assert not any("LocalCollision" in fact["qualified_name"]
                       for record in records
                       for fact in record["declaration_facts"])
        expected_conflict = [
            {
                "qualified_name": alias_entity,
                "source_digest": source_digest,
                "declarations": [
                    {"configuration": "debug", "kind": "class"},
                    {"configuration": "product", "kind": "using_alias"},
                ],
            },
            {
                "qualified_name": enum_entity,
                "source_digest": source_digest,
                "declarations": [
                    {"configuration": "debug", "kind": "scoped_enum"},
                    {"configuration": "product", "kind": "class"},
                ],
            },
            {
                "qualified_name": entity,
                "source_digest": source_digest,
                "declarations": [
                    {"configuration": "debug", "kind": "class"},
                    {"configuration": "product", "kind": "typedef"},
                ],
            },
        ]
        assert product["declaration_conflicts"] == expected_conflict
        assert debug["declaration_conflicts"] == expected_conflict
        assert compatible_entity not in {
            conflict["qualified_name"] for conflict in expected_conflict}
        assert debug["status"] == "changes"
        assert debug["desired_state"]["status"] == "known"
        assert debug["desired_state"]["complete"] is True
        assert debug["operations"]
        safe = [operation for operation in debug["operations"]
                if operation["disposition"] == "apply"]
        assert {(operation["item_kind"], operation["spelling"])
                for operation in safe} == {
                    ("include", "#include <vector>"),
                    ("forward_declaration",
                     "namespace sem005_corpus { class CompatibleRecord; }")}
        blocked = [operation for operation in debug["operations"]
                   if operation["reason_code"] == "declaration_kind_conflict"]
        assert blocked
        assert all(operation["disposition"] == "report"
                   for operation in blocked)
        assert any(operation["subject_action"] == "remove" and
                   operation["item_kind"] == "include"
                   for operation in blocked)
        desired_spellings = {
            (item["item_kind"], item["spelling"])
            for item in debug["desired_state"]["items"]}
        assert ("include", "#include <vector>") in desired_spellings
        assert ("include", '#include "tests/sem005/variant_provider.hpp"') \
            in desired_spellings
        desired_items = debug["desired_state"]["items"]
        provider_index = next(
            index for index, item in enumerate(desired_items)
            if item["spelling"] ==
               '#include "tests/sem005/variant_provider.hpp"')
        first_forward_index = next(
            index for index, item in enumerate(desired_items)
            if item["item_kind"] == "forward_declaration")
        assert provider_index < first_forward_index, desired_items
        assert not any(item_kind == "forward_declaration" and
                       ("Handle" in spelling and "CompatibleRecord" not in spelling)
                       for item_kind, spelling in desired_spellings)
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

        # Two source spellings that are symlinks to one physical file must
        # aggregate by Clang FileEntry identity and expose the same conflicts.
        product_alias = temporary / "product-alias.cc"
        debug_alias = temporary / "debug-alias.cc"
        product_alias.symlink_to(SOURCE)
        debug_alias.symlink_to(SOURCE)
        alias_product_db = temporary / "alias-product"
        alias_debug_db = temporary / "alias-debug"
        write_database(
            alias_product_db,
            include_args + ["-c", str(product_alias)], product_alias)
        write_database(
            alias_debug_db,
            include_args + ["-DSEM005_DEBUG_CONFIGURATION=1", "-c",
                            str(debug_alias)], debug_alias)
        alias_records = parse_records(run(
            driver_command(alias_product_db, alias_debug_db), env=analyzer_env))
        assert {record["file"]["path"] for record in alias_records} == {
            str(product_alias), str(debug_alias)}
        assert {record["source_identity"] for record in alias_records} == {
            source_identity}
        assert all(record["declaration_conflicts"] == expected_conflict
                   for record in alias_records)

        # The same relative spelling in different compilation directories
        # denotes different physical files and must never be combined.
        relative_product_root = temporary / "relative-product-root"
        relative_debug_root = temporary / "relative-debug-root"
        relative_source = Path("same") / "consumer.cc"
        for source_root in (relative_product_root, relative_debug_root):
            (source_root / relative_source).parent.mkdir(parents=True)
            (source_root / relative_source).write_bytes(SOURCE.read_bytes())
        relative_product_db = temporary / "relative-product-db"
        relative_debug_db = temporary / "relative-debug-db"
        relative_args = [str(COMPILER), "-I", str(ROOT), "-std=c++17"]
        write_database(
            relative_product_db,
            relative_args + ["-c", str(relative_source)], relative_source,
            relative_product_root)
        write_database(
            relative_debug_db,
            relative_args + ["-DSEM005_DEBUG_CONFIGURATION=1", "-c",
                             str(relative_source)], relative_source,
            relative_debug_root)
        relative_records = parse_records(run(
            driver_command(relative_product_db, relative_debug_db),
            env=analyzer_env))
        assert {record["file"]["path"] for record in relative_records} == {
            str(relative_source)}
        assert len({record["source_identity"]
                    for record in relative_records}) == 2
        assert all(record["declaration_conflicts"] == []
                   for record in relative_records)

        # Feed the aggregated desired state through the actual fixer. The
        # retained provider must remain in the include block before the safe
        # forward declaration, and the result must compile in both variants.
        fixer_root = temporary / "fixer-tree"
        fixer_fixture = fixer_root / "tests" / "sem005"
        fixer_fixture.mkdir(parents=True)
        fixed_source = fixer_fixture / "variant_consumer.cc"
        shutil.copy2(SOURCE, fixed_source)
        shutil.copy2(ROOT / "tests/sem005/variant_provider.hpp",
                     fixer_fixture / "variant_provider.hpp")
        fixed = run(
            [sys.executable, str(ROOT / "fix_includes.py"), "--nosafe_headers"],
            cwd=fixer_root, input=fixer_report(debug, fixed_source))
        assert fixed.returncode == 0, (fixed.stdout, fixed.stderr)
        fixed_text = fixed_source.read_text()
        assert fixed_text.index("#include <vector>") < fixed_text.index(
            '#include "tests/sem005/variant_provider.hpp"')
        assert fixed_text.index(
            '#include "tests/sem005/variant_provider.hpp"') < \
            fixed_text.index("class CompatibleRecord;")
        for define in ([], ["-DSEM005_DEBUG_CONFIGURATION=1"]):
            result = run([
                str(COMPILER), "-I", str(fixer_root), "-std=c++17", *define,
                "-c", str(fixed_source), "-o",
                str(temporary / ("fixed-%d.o" % len(define)))])
            assert result.returncode == 0, result.stderr

        # Schema v1 remains the direct-output default and has no v2 fields.
        legacy = run([str(IWYU), "-Xiwyu", "--output_format=json",
                      "-I", str(ROOT), "-std=c++17", str(SOURCE)])
        legacy_record = json.loads(legacy.stdout.splitlines()[0])
        assert legacy_record["schema_version"] == 1
        assert "source_identity" not in legacy_record
        assert "source_digest" not in legacy_record
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

        unsafe_enum = temporary / "unsafe-enum.cc"
        unsafe_enum.write_text(
            "namespace sem005_corpus { enum class EnumClassHandle; }\n"
            "#include \"tests/sem005/variant_provider.hpp\"\n"
            "sem005_corpus::EnumClassHandle* value;\n")
        debug_enum = run(include_args + ["-DSEM005_DEBUG_CONFIGURATION=1",
                                         "-c", str(unsafe_enum), "-o",
                                         str(temporary / "unsafe-enum-debug.o")])
        assert debug_enum.returncode == 0, debug_enum.stderr
        product_enum = run(include_args + ["-c", str(unsafe_enum), "-o",
                                        str(temporary / "unsafe-enum-product.o")])
        assert product_enum.returncode != 0

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
        template_facts = [
            fact for fact in kinds_record["declaration_facts"]
            if fact["qualified_name"] ==
               "sem005_kinds::ClassTemplateKind"]
        assert len(template_facts) == 1
        assert template_facts[0]["kind"] == "class_template"
        template_line = next(
            number for number, line in enumerate(
                (ROOT / "tests/sem005/declaration_kinds.hpp").read_text().splitlines(), 1)
            if line == "template <typename T> class ClassTemplateKind {};")
        assert template_facts[0]["declaration"] == {
            "path": str(ROOT / "tests/sem005/declaration_kinds.hpp"),
            "line": template_line,
            "column": 29,
        }

        # Malformed and forged facts fail atomically before any JSON is emitted.
        fake = temporary / "fake-iwyu.py"
        write_fake_analyzer(fake)
        matrix_env = os.environ.copy()
        matrix_env["IWYU_BINARY"] = str(fake)
        matrix_env["SEM005_MALFORMED"] = "kind_matrix"
        matrix_records = parse_records(run(command, env=matrix_env))
        matrix_conflicts = {
            conflict["qualified_name"]
            for conflict in matrix_records[0]["declaration_conflicts"]}
        assert matrix_conflicts == {
            "matrix::AliasConflict",
            "matrix::EnumKindConflict",
            "matrix::RecordUnionConflict",
            "matrix::TemplateConflict",
        }
        assert all(record["declaration_conflicts"] ==
                   matrix_records[0]["declaration_conflicts"]
                   for record in matrix_records)

        for mode, message in [
                ("unknown_kind", "value is outside schema enum"),
                ("extra_fact_key", "unexpected field spelling"),
                ("forged_conflict", "must be an empty array"),
                ("missing_tool", "missing required field tool"),
                ("missing_operations", "missing required field operations"),
                ("semantic_status", "changes record contains no applied edit"),
                ("identity_forgery",
                 "source_identity does not match file.path physical identity"),
                ("path_forgery",
                 "source_identity does not match file.path physical identity"),
                ("different_digest",
                 "same physical source has different producer-computed bytes")]:
            env = os.environ.copy()
            env["IWYU_BINARY"] = str(fake)
            env["SEM005_MALFORMED"] = mode
            malformed = run(command, env=env)
            assert malformed.returncode != 0
            assert malformed.stdout == ""
            assert message in malformed.stderr, malformed.stderr

        race_source = temporary / "source-race.cc"
        race_source.write_text("struct Race {};\nRace* value;\n")
        race_product = temporary / "race-product"
        race_debug = temporary / "race-debug"
        race_arguments = [str(COMPILER), "-c", str(race_source)]
        write_database(race_product, race_arguments, race_source)
        write_database(race_debug, race_arguments, race_source)
        race_env = os.environ.copy()
        race_env["IWYU_BINARY"] = str(fake)
        race_env["SEM005_MALFORMED"] = "source_race"
        raced = run(driver_command(race_product, race_debug), env=race_env)
        assert raced.returncode != 0
        assert raced.stdout == ""
        assert "source_digest does not match current source bytes" in raced.stderr

    print("PASS: SEM-005 typed declaration conflicts and compile controls")


if __name__ == "__main__":
    main()
