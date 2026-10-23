"""Opaque schema-v2 occurrence relocation contracts."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "occurrence_relocation"
IWYU = Path(sys.argv[1]).resolve()
CXX = Path(sys.argv[2]).resolve()
DRIVER = ROOT / "iwyu_tool.py"
FIXER = ROOT / "fix_includes.py"
SCHEMA = json.loads((ROOT / "docs/output-v2.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
sys.path.insert(0, str(ROOT / "tests"))
from validate_output_schema import semantic_errors  # noqa: E402

spec = importlib.util.spec_from_file_location("iwyu_tool_contract", DRIVER)
iwyu_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iwyu_tool)


def run(command, cwd, **kwargs):
    return subprocess.run(
        [str(argument) for argument in command], cwd=cwd, text=True,
        capture_output=True, **kwargs)


def analyzer_command(root, schema=None):
    command = [IWYU]
    if schema is not None:
        command += [
            "-Xiwyu", "--output_format=json",
            "-Xiwyu", f"--output_schema={schema}",
        ]
    command += [
        "-Xiwyu", f"--check_also={root / 'tests/occurrence_relocation/fixture.hpp'}",
        f"-I{root}", "-std=c++17", "-DCOMPILER1", "-DCOMPILER2",
        root / "tests/occurrence_relocation/driver.cc",
    ]
    return command


def parse(output):
    result = [json.loads(line) for line in output.splitlines()]
    assert result
    return result


def fixture_record(records):
    return next(record for record in records
                if record["file"]["path"].endswith(
                    "tests/occurrence_relocation/fixture.hpp"))


def validate_record(record):
    VALIDATOR.validate(record)
    assert semantic_errors(record) == []
    source = Path(record["source_identity"]).read_bytes()
    iwyu_tool._validate_declaration_facts(record, source)


def renumber(record):
    record["operations"].sort(key=iwyu_tool._operation_sort_key)
    for index, operation in enumerate(record["operations"], 1):
        operation["id"] = f"op-{index}"


def expect_protocol_error(record, expected):
    source = Path(record["source_identity"]).read_bytes()
    try:
        iwyu_tool._validate_declaration_facts(record, source)
    except iwyu_tool.ProtocolError as error:
        assert expected in str(error), str(error)
    else:
        raise AssertionError("invalid occurrence model was accepted")


def tree_digest(root):
    digest = hashlib.sha256()
    for path in sorted((root / "tests/occurrence_relocation").iterdir()):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def compile_configs(root, output_root):
    source = root / "tests/occurrence_relocation/driver.cc"
    for name, definitions in (
            ("none", []), ("c1", ["-DCOMPILER1"]),
            ("c2", ["-DCOMPILER2"]),
            ("both", ["-DCOMPILER1", "-DCOMPILER2"])):
        result = run([
            CXX, f"-I{root}", "-std=c++17", *definitions,
            "-c", source, "-o", output_root / f"{name}.o",
        ], root)
        assert result.returncode == 0, (name, result.stderr)
def assert_logical_item_keys():


    include_variants = [
        {'item_kind': 'include',
         'spelling': '  # include "tests/occurrence_relocation/provider.hpp" // retained physical'},
        {'item_kind': 'include',
         'spelling': '\t#\tinclude /* between */ "tests/occurrence_relocation/provider.hpp"\t/* c1 */'},
        {'item_kind': 'include',
         'spelling': '#include "tests/occurrence_relocation/provider.hpp" /* fake #include "evil.hpp" */'},
    ]
    include_keys = [iwyu_tool._logical_item_key(item)
                    for item in include_variants]
    assert include_keys[0] == include_keys[1] == include_keys[2]
    assert include_keys[0] != iwyu_tool._logical_item_key({
        'item_kind': 'include', 'spelling': '#include "other.hpp"'})
    for malicious in (
            'const char* text = "#include \\"provider.hpp\\"";',
            '#include "provider.hpp" retained_text',
            '/* #include "provider.hpp"'):
        try:
            iwyu_tool._logical_item_key(
                {'item_kind': 'include', 'spelling': malicious})
        except iwyu_tool.ProtocolError:
            pass
        else:
            raise AssertionError('malicious include text acquired a logical key')

    computed_variants = [
        {'item_kind': 'include',
         'spelling': '#include CPU_HEADER(gc/shenandoah/provider)'},
        {'item_kind': 'include',
         'spelling': ('  # include /* before */ CPU_HEADER ( gc / shenandoah / '
                      'provider ) // retained physical')},
        {'item_kind': 'include',
         'spelling': '#inc\\\nlude CPU_HEA\\\nDER(gc/shenandoah/provider)'},
    ]
    computed_keys = [iwyu_tool._logical_item_key(item)
                     for item in computed_variants]
    assert computed_keys[0] == computed_keys[1] == computed_keys[2]
    assert computed_keys[0][1][0] == 'computed'
    assert computed_keys[0] != iwyu_tool._logical_item_key({
        'item_kind': 'include',
        'spelling': '#include CPU_HEADER(gc/shenandoah/other)'})
    assert computed_keys[0] != iwyu_tool._logical_item_key({
        'item_kind': 'include', 'spelling': '#include "provider"'})
    for malformed in (
            '#include', '#include /* comment only */',
            '#include CPU_HEADER(gc/provider',
            '#include CPU_HEADER(gc/provider)]',
            '#include CPU_HEADER(gc/provider); injected()',
            '#include 42', '#include CPU_HEADER(gc/provider) \\'):
        try:
            iwyu_tool._logical_item_key(
                {'item_kind': 'include', 'spelling': malformed})
        except iwyu_tool.ProtocolError:
            pass
        else:
            raise AssertionError(
                'malformed computed include acquired a logical key: %r' %
                malformed)

    forward_variants = [
        {'item_kind': 'forward_declaration',
         'spelling': 'class OccurrenceRelocatedDeclaration; // retained'},
        {'item_kind': 'forward_declaration',
         'spelling': '\tclass /* split */ OccurrenceRelocatedDeclaration ;'},
        {'item_kind': 'forward_declaration',
         'spelling': 'class OccurrenceRelocatedDeclaration; /* class Evil; */'},
    ]
    forward_keys = [iwyu_tool._logical_item_key(item)
                    for item in forward_variants]
    assert forward_keys[0] == forward_keys[1] == forward_keys[2]
    assert forward_keys[0] != iwyu_tool._logical_item_key({
        'item_kind': 'forward_declaration', 'spelling': 'class Other;'})
    assert forward_keys[0] != iwyu_tool._logical_item_key({
        'item_kind': 'forward_declaration',
        'spelling': 'class OccurrenceRelocatedDeclaration; int injected;'})
    canonical = [{
        'item_kind': 'forward_declaration',
        'spelling': ('namespace occurrence { '
                     'class OccurrenceRelocatedDeclaration; }'),
    }]
    desired_index = iwyu_tool._forward_desired_index(canonical)
    canonical_key = iwyu_tool._logical_item_key(canonical[0], desired_index)
    physical = {
        'item_kind': 'forward_declaration',
        'spelling': '\tclass /* split */ OccurrenceRelocatedDeclaration ;',
        'source_range': {'status': 'unavailable', 'value': None},
    }
    assert iwyu_tool._logical_item_key(physical, desired_index) == canonical_key
    injected = dict(physical)
    injected['spelling'] += ' int injected;'
    assert iwyu_tool._logical_item_key(injected, desired_index) != canonical_key
    template_index = iwyu_tool._forward_desired_index([{
        'item_kind': 'forward_declaration',
        'spelling': 'template <typename T> class TemplateExample;',
    }])
    different_template = {
        'item_kind': 'forward_declaration',
        'spelling': ('template <typename T, typename U> '
                     'class TemplateExample;'),
        'source_range': {'status': 'unavailable', 'value': None},
    }
    assert iwyu_tool._logical_item_key(
        different_template, template_index) != (
            'forward_declaration', next(iter(template_index.values())))

def main():
    assert_logical_item_keys()
    direct_one = run(analyzer_command(ROOT, 2), ROOT)
    direct_two = run(analyzer_command(ROOT, 2), ROOT)
    assert direct_one.returncode == direct_two.returncode == 0
    assert direct_one.stdout == direct_two.stdout
    record = fixture_record(parse(direct_one.stdout))
    validate_record(record)

    computed_add_record = copy.deepcopy(record)
    computed_add = copy.deepcopy(record["operations"][0])
    computed_add.update({
        "subject_action": "add",
        "disposition": "apply",
        "spelling": "#include CPU_HEADER(gc/shenandoah/provider)",
        "source_range": {"status": "not_applicable", "value": None},
        "reason_code": "desired_item_missing",
        "reason": "Add the canonical computed include.",
        "symbols": [],
        "provider": {
            "selected": {"status": "unavailable", "value": None},
            "candidates": {"status": "unavailable", "value": None},
            "chain": {"status": "unavailable", "value": None},
        },
        "edit_condition": {"status": "not_applicable", "value": None},
        "policy": {"status": "unavailable", "value": None},
        "multi_config": {"status": "not_applicable", "value": None},
    })
    computed_add_record["operations"] = [computed_add]
    computed_add_record["desired_state"]["items"] = [{
        "item_kind": "include",
        "spelling": computed_add["spelling"],
        "symbols": [],
    }]
    computed_add_record["status"] = "changes"
    renumber(computed_add_record)
    validate_record(computed_add_record)
    mismatched_computed_add = copy.deepcopy(computed_add_record)
    mismatched_computed_add["desired_state"]["items"][0]["spelling"] = (
        "# include CPU_HEADER(gc/shenandoah/provider)")
    expect_protocol_error(
        mismatched_computed_add,
        "computed add does not match analyzer canonical desired line")

    operations = record["operations"]
    assert [(operation["subject_action"], operation["disposition"],
             operation["item_kind"])
            for operation in operations] == [
        ("remove", "apply", "include"),
        ("remove", "apply", "include"),
        ("remove", "apply", "forward_declaration"),
        ("remove", "apply", "forward_declaration"),
        ("keep", "keep", "include"),
        ("keep", "keep", "forward_declaration"),
    ]
    assert {operation["spelling"] for operation in operations} == {
        '  # include "tests/occurrence_relocation/provider.hpp" // retained physical',
        '\t#\tinclude "tests/occurrence_relocation/provider.hpp"\t/* c1 physical */',
        '#include "tests/occurrence_relocation/provider.hpp" /* c2 physical */',
        'class OccurrenceRelocatedDeclaration; // retained physical',
        '\tclass OccurrenceRelocatedDeclaration;\t/* c1 physical */',
        'class OccurrenceRelocatedDeclaration; /* c2 physical */',
    }
    removals = [operation for operation in operations
                if operation["subject_action"] == "remove"]
    assert [operation["source_range"]["value"]["start"]["line"]
            for operation in removals] == [9, 14, 10, 15]
    assert [operation["edit_condition"]["value"]["expression"]
            for operation in removals] == [
        "defined(COMPILER1)", "defined(COMPILER2)",
        "defined(COMPILER1)", "defined(COMPILER2)",
    ]
    identities = {
        (json.dumps(operation["source_range"], sort_keys=True),
         json.dumps(operation["edit_condition"], sort_keys=True))
        for operation in operations
    }
    assert len(identities) == 6
    desired = {(item["item_kind"], item["spelling"])
               for item in record["desired_state"]["items"]}
    assert ("include", '#include "tests/occurrence_relocation/provider.hpp"') in desired
    relocated = copy.deepcopy(record)
    retained_forward = next(
        operation for operation in relocated["operations"]
        if operation["subject_action"] == "keep" and
        operation["item_kind"] == "forward_declaration")
    retained_forward["subject_action"] = "add"
    retained_forward["disposition"] = "apply"
    retained_forward["source_range"] = {
        "status": "not_applicable", "value": None}
    retained_forward["reason_code"] = "desired_item_missing"
    retained_forward["reason"] = "Relocate to one unconditional declaration."
    renumber(relocated)
    validate_record(relocated)

    schema_one_shape = copy.deepcopy(relocated)
    schema_one_shape["schema_version"] = 1
    assert semantic_errors(schema_one_shape)

    assert ("forward_declaration",
            "class OccurrenceRelocatedDeclaration;") in desired

    contradictory = copy.deepcopy(record)
    contradictory["desired_state"]["items"] = [
        item for item in contradictory["desired_state"]["items"]
        if item["item_kind"] != "forward_declaration"]
    expect_protocol_error(contradictory, "group contradicts desired state")

    duplicate = copy.deepcopy(record)
    duplicate["operations"].append(copy.deepcopy(duplicate["operations"][0]))
    renumber(duplicate)
    expect_protocol_error(duplicate, "duplicate semantic operation identity")

    ambiguous = copy.deepcopy(record)
    extra = copy.deepcopy(ambiguous["operations"][0])
    extra["edit_condition"] = copy.deepcopy(
        ambiguous["operations"][1]["edit_condition"])
    extra["subject_action"] = "keep"
    extra["disposition"] = "keep"
    extra["reason_code"] = "ambiguous_occurrence_fixture"
    extra["multi_config"] = {"status": "not_applicable", "value": None}
    ambiguous["operations"].append(extra)
    renumber(ambiguous)
    expect_protocol_error(ambiguous, "ambiguously identify the same present occurrence")

    legacy = run(analyzer_command(ROOT, 1), ROOT)
    assert legacy.returncode == 0, legacy.stderr
    legacy_record = fixture_record(parse(legacy.stdout))
    assert legacy_record["status"] == "skipped"
    assert legacy_record["operations"] == []
    assert legacy_record["desired_state"] == {
        "status": "unavailable", "complete": False, "items": None}
    assert [diagnostic["code"] for diagnostic in legacy_record["diagnostics"]
            if diagnostic["code"] == "duplicate_occurrence_unrepresentable"] == [
        "duplicate_occurrence_unrepresentable"]

    with tempfile.TemporaryDirectory(prefix="occurrence-v2-",
                                     dir=ROOT / "build") as directory:
        temporary_root = Path(directory)
        shutil.copytree(CORPUS,
                        temporary_root / "tests/occurrence_relocation")
        compile_configs(temporary_root, temporary_root)

        database = [{
            "directory": str(temporary_root),
            "file": str(temporary_root / "tests/occurrence_relocation/driver.cc"),
            "arguments": [
                str(CXX), f"-I{temporary_root}", "-std=c++17",
                "-DCOMPILER1", "-DCOMPILER2",
                str(temporary_root / "tests/occurrence_relocation/driver.cc"),
            ],
        }]
        (temporary_root / "compile_commands.json").write_text(
            json.dumps(database))
        environment = os.environ.copy()
        environment["IWYU_BINARY"] = str(IWYU)
        driver = run([
            sys.executable, DRIVER, "-o", "json", "--output-schema", "2",
            "-p", temporary_root, "--", "-Xiwyu",
            f"--check_also={temporary_root / 'tests/occurrence_relocation/fixture.hpp'}",
        ], ROOT, env=environment)
        assert driver.returncode == 0, driver.stderr
        driver_record = fixture_record(parse(driver.stdout))
        validate_record(driver_record)

        first_text = run(analyzer_command(temporary_root), temporary_root)
        assert first_text.returncode == 0, first_text.stderr
        first_fix = run([
            sys.executable, FIXER, "--nosafe_headers",
        ], temporary_root, input=first_text.stderr)
        assert first_fix.returncode == 0, (first_fix.stdout, first_fix.stderr)
        fixed = temporary_root / "tests/occurrence_relocation/fixture.hpp"
        fixed_text = fixed.read_text()
        assert fixed_text.count("class OccurrenceRelocatedDeclaration;") == 1
        assert fixed_text.count(
            '"tests/occurrence_relocation/provider.hpp"') == 1
        compile_configs(temporary_root, temporary_root)

        before_second = tree_digest(temporary_root)
        second_text = run(analyzer_command(temporary_root), temporary_root)
        assert second_text.returncode == 0, second_text.stderr
        second_fix = run([
            sys.executable, FIXER, "--nosafe_headers",
        ], temporary_root, input=second_text.stderr)
        assert second_fix.returncode == 0, (second_fix.stdout, second_fix.stderr)
        assert tree_digest(temporary_root) == before_second
        compile_configs(temporary_root, temporary_root)

        second_json = run(analyzer_command(temporary_root, 2), temporary_root)
        assert second_json.returncode == 0, second_json.stderr
        for second_record in parse(second_json.stdout):
            validate_record(second_record)
            assert not any(operation["disposition"] == "apply"
                           for operation in second_record["operations"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
