"""Opaque contracts for strict versioned project-policy parsing."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
IWYU = Path(sys.argv[1]).resolve()
SOURCE = "tests/native_json/clean.cc"


def invoke(path, *args, json_output=True):
    command = [str(IWYU)]
    if json_output:
        command.extend(["-Xiwyu", "--output_format=json"])
    command.extend(["-Xiwyu", f"--project_policy={path}"])
    for arg in args:
        command.extend(["-Xiwyu", arg])
    command.extend(["-I", ".", "-std=c++17", SOURCE])
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True)


def with_policy(data, *args, json_output=True):
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
    with tempfile.NamedTemporaryFile(mode=mode, suffix=".yaml", dir=ROOT,
                                     delete=False, **kwargs) as stream:
        stream.write(data)
        path = Path(stream.name)
    try:
        return invoke(path, *args, json_output=json_output)
    finally:
        path.unlink()


def expect_ok(data, *args):
    result = with_policy(data, *args)
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    expected_schema = 2 if "--output_schema=2" in args else 1
    assert records and all(record["schema_version"] == expected_schema
                           for record in records)
    return records


def with_policy_for_source(data, source, *args):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", dir=ROOT,
                                     delete=False) as stream:
        stream.write(data)
        path = Path(stream.name)
    try:
        command = [str(IWYU), "-Xiwyu", "--output_format=json",
                   "-Xiwyu", f"--project_policy={path}"]
        for arg in args:
            command.extend(["-Xiwyu", arg])
        command.extend(["-I", ".", "-std=c++17", source])
        return subprocess.run(command, cwd=ROOT, text=True,
                              capture_output=True)
    finally:
        path.unlink()


def expect_error(data, fragment, *args):
    result = with_policy(data, *args)
    assert result.returncode != 0
    assert result.stdout == ""
    assert fragment in result.stderr, result.stderr
    assert "project policy" in result.stderr.lower(), result.stderr
    return result


def policy(match="includer: { glob: never-match }", action="deny_add",
           rule_id="parser-rule", schema_version=1):
    return f"""schema_version: {schema_version}
rules:
  - id: {rule_id}
    action: {action}
    match:
      {match}
"""

def removal_operation(policy_text,
                      source="tests/native_json/policy007_source.cc",
                      spelling_suffix="policy007_unused.hpp"):
    result = with_policy_for_source(
        policy_text, source, "--output_schema=2")
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    operations = [operation for record in records
                  for operation in record["operations"]
                  if operation["item_kind"] == "include" and
                  spelling_suffix in operation["spelling"]]
    assert len(operations) == 1, operations
    return operations[0]


def main():

    help_result = subprocess.run([str(IWYU), "--help"], cwd=ROOT, text=True,
                                 capture_output=True)
    assert help_result.returncode == 0, help_result.stderr
    assert "project-policy-enabled-actions: " \
           "allow_add,allow_remove,deny_add,deny_remove,keep" in help_result.stdout
    # Empty YAML and JSON-compatible policies are valid no-ops.
    expect_ok("schema_version: 1\nrules: []\n")

    # Version 2 adds one exact scope fact and bounded allow_add. Version 1
    # remains closed and rejects both additions to its grammar.
    expect_ok("schema_version: 2\nrules: []\n")
    expect_ok(policy("edit_lexical_scope: non_file", schema_version=2))

    # Version 3 adds only replacement-backed allow_remove. Older schemas remain
    # closed, and unrestricted removal authorization is never enabled.
    expect_ok("schema_version: 3\nrules: []\n")
    replacement_allow = policy(
        "replacement: forward_declaration",
        action="allow_remove", schema_version=3)
    expect_ok(replacement_allow, "--output_schema=2")
    expect_error(policy(action="allow_remove", schema_version=2),
                 "unknown action 'allow_remove'")
    expect_error(policy("replacement: forward_declaration", schema_version=2),
                 "unknown match key 'replacement'")
    expect_error(policy("includer: { glob: '*.hpp' }",
                        action="allow_remove", schema_version=3),
                 "allow_remove requires replacement: forward_declaration")
    expect_error(policy("replacement: include",
                        action="allow_remove", schema_version=3),
                 "unknown replacement 'include'")
    expect_error(policy("replacement: true",
                        action="allow_remove", schema_version=3),
                 "'replacement' must be a string scalar")
    expect_error(replacement_allow,
                 "allow_remove requires native structured output schema 2")
    allow_remove_text = with_policy(replacement_allow, json_output=False)
    assert allow_remove_text.returncode != 0 and allow_remove_text.stdout == ""
    assert "allow_remove requires native structured output schema 2" in \
        allow_remove_text.stderr

    # A definite absence of replacement falls through to a later deny. A
    # computed physical include with a potential replacement remains unknown and
    # suppresses without claiming policy provenance. A definite earlier deny
    # still wins.
    allow_first = """schema_version: 3
rules:
  - id: replacement-first
    action: allow_remove
    match:
      includee: { glob: tests/native_json/policy007_unused.hpp }
      replacement: forward_declaration
  - id: deny-fallback
    action: deny_remove
    match:
      includee: { glob: tests/native_json/policy007_unused.hpp }
"""
    fallback_removal = removal_operation(allow_first)
    assert fallback_removal["disposition"] == "suppress"
    assert fallback_removal["reason_code"] == "project_policy_deny_remove"
    assert fallback_removal["policy"]["value"]["rule_id"] == "deny-fallback"
    computed_allow = """schema_version: 3
rules:
  - id: computed-replacement
    action: allow_remove
    match:
      includee: { glob: tests/native_json/policy_allow_remove_provider.inline.hpp }
      replacement: forward_declaration
"""
    unknown_removal = removal_operation(
        computed_allow,
        "tests/native_json/policy_allow_remove_computed.hpp",
        "POLICY_ALLOW_REMOVE_PROVIDER")
    assert unknown_removal["disposition"] == "suppress"
    assert unknown_removal["reason_code"] == "project_policy_replacement_unknown"
    assert unknown_removal["policy"] == {"status": "unavailable", "value": None}
    assert unknown_removal["symbols"] == []
    deny_first = """schema_version: 3
rules:
  - id: deny-first
    action: deny_remove
    match:
      includee: { glob: tests/native_json/policy007_unused.hpp }
  - id: replacement-later
    action: allow_remove
    match:
      includee: { glob: tests/native_json/policy007_unused.hpp }
      replacement: forward_declaration
"""
    denied_removal = removal_operation(deny_first)
    assert denied_removal["disposition"] == "suppress"
    assert denied_removal["reason_code"] == "project_policy_deny_remove"
    assert denied_removal["policy"]["value"]["decision"] == "deny"

    unknown_condition_no_replacement = """schema_version: 3
rules:
  - id: unknown-condition-but-no-replacement
    action: allow_remove
    match:
      includee: { glob: tests/native_json/policy_active_condition_empty.hpp }
      active_condition: POLICY_ACTIVE_REPEATED_REMOVE
      replacement: forward_declaration
  - id: definite-deny-fallback
    action: deny_remove
    match:
      includee: { glob: tests/native_json/policy_active_condition_empty.hpp }
"""
    mismatch_result = with_policy_for_source(
        unknown_condition_no_replacement,
        "tests/native_json/policy_active_condition_repeated_remove.cc",
        "--output_schema=2",
        "--check_also=tests/native_json/"
        "policy_active_condition_repeated_remove.hpp")
    assert mismatch_result.returncode == 0, mismatch_result.stderr
    mismatch_records = [json.loads(line)
                        for line in mismatch_result.stdout.splitlines()]
    mismatch_record = next(
        record for record in mismatch_records
        if record["file"]["path"].endswith(
            "policy_active_condition_repeated_remove.hpp"))
    mismatch_removal = next(
        operation for operation in mismatch_record["operations"]
        if operation["subject_action"] == "remove")
    assert mismatch_removal["reason_code"] == "project_policy_deny_remove"
    assert mismatch_removal["policy"]["value"]["rule_id"] == (
        "definite-deny-fallback")

    bounded_allow = policy(
        "active_condition: 'defined(FEATURE)'\n"
        "      edit_lexical_scope: file",
        action="allow_add", schema_version=2)
    expect_ok(bounded_allow, "--output_schema=2")
    expect_error(policy(action="allow_add"), "unknown action 'allow_add'")
    expect_error(policy("edit_lexical_scope: file"),
                 "unknown match key 'edit_lexical_scope'")
    expect_error(policy("edit_lexical_scope: translation_unit",
                        schema_version=2),
                 "unknown edit_lexical_scope 'translation_unit'")
    expect_error(policy("edit_lexical_scope: true", schema_version=2),
                 "'edit_lexical_scope' must be a string scalar")
    for broad_match in (
            "edit_lexical_scope: file",
            "active_condition: 'defined(FEATURE)'",
            "active_condition: 'defined(FEATURE)'\n"
            "      edit_lexical_scope: non_file"):
        expect_error(policy(broad_match, action="allow_add", schema_version=2),
                     "allow_add requires exact active_condition and "
                     "edit_lexical_scope: file")

    # An allow policy cannot silently degrade through text or JSON schema 1.
    allow_text = with_policy(bounded_allow, json_output=False)
    assert allow_text.returncode != 0 and allow_text.stdout == ""
    assert "allow_add requires native structured output schema 2" in \
        allow_text.stderr
    expect_error(bounded_allow,
                 "allow_add requires native structured output schema 2")
    expect_ok('{"schema_version":1,"rules":[]}\n')

    # Every currently evaluable match dimension parses transactionally.
    expect_ok(
        """schema_version: 1
rules:
  - id: every-supported-dimension
    action: deny_add
    match:
      includer: { regex: "never[/]matches[.]cc" }
      includee: { glob: "project/*.hpp" }
      file_kind: header
      active_condition: "defined(FEATURE)"
      symbol: "project::Type"
      configuration: fastdebug
""",
        "--configuration=fastdebug")

    # Closed schemas reject unknown and duplicate keys at every level.
    expect_error("schema_version: 1\nrules: []\nextra: true\n",
                 "unknown top-level key 'extra'")
    expect_error("schema_version: 1\nschema_version: 1\nrules: []\n",
                 "duplicate top-level key 'schema_version'")
    expect_error(policy() + "    extra: value\n", "unknown rule key 'extra'")
    expect_error(policy("unknown: value"), "unknown match key 'unknown'")
    expect_error(policy("includer: { glob: x, extra: y }"),
                 "unknown pattern key 'extra'")
    expect_error(
        """schema_version: 1
rules:
  - id: duplicate-rule-key
    id: duplicate-rule-key-2
    action: deny_add
    match: { file_kind: header }
""", "duplicate rule key 'id'")
    expect_error(
        """schema_version: 1
rules:
  - id: duplicate-match-key
    action: deny_add
    match:
      file_kind: header
      file_kind: source
""", "duplicate match key 'file_kind'")
    expect_error(policy("includer: { glob: x, glob: y }"),
                 "duplicate pattern key 'glob'")

    # Required keys, exact node shapes and non-empty values fail closed.
    expect_error("rules: []\n", "missing required top-level key 'schema_version'")
    expect_error("schema_version: 1\n", "missing required top-level key 'rules'")
    expect_error("schema_version: 1\nrules: null\n", "'rules' must be a sequence")
    expect_error("schema_version: 1\nrules: {}\n", "'rules' must be a sequence")
    expect_error("schema_version: 1\nrules: [value]\n", "rule 1 must be a mapping")
    expect_error(
        "schema_version: 1\nrules:\n  - action: deny_add\n"
        "    match: { file_kind: header }\n", "missing required rule key 'id'")
    expect_error(
        "schema_version: 1\nrules:\n  - id: missing-action\n"
        "    match: { file_kind: header }\n", "missing required rule key 'action'")
    expect_error(
        "schema_version: 1\nrules:\n  - id: missing-match\n"
        "    action: deny_add\n", "missing required rule key 'match'")
    expect_error(policy("{}"), "match must contain at least one dimension")
    expect_error(policy("includer: scalar"), "'includer' must be a mapping")
    expect_error(policy("includer: {}"),
                 "must contain exactly one of 'glob' or 'regex'")
    expect_error(policy("includer: { glob: x, regex: x }"),
                 "must contain exactly one of 'glob' or 'regex'")
    for field in ("active_condition", "symbol"):
        expect_error(policy(f"{field}: ''"),
                     f"'{field}' must be a non-empty scalar")
    expect_error(policy("file_kind: |\n        header"),
                 "'file_kind' must be a scalar")
    for field, value in (("active_condition", "true"), ("symbol", "null"),
                         ("configuration", "42")):
        expect_error(policy(f"{field}: {value}"),
                     f"'{field}' must be a string scalar")
    expect_error(policy("includer: { glob: true }"),
                 "'glob' must be a string scalar")
    expect_error(policy("includee: { regex: 3.14 }"),
                 "'regex' must be a string scalar")
    for numeric in (".inf", "-.Inf", ".NaN", "1e9999", "-1e9999",
                    "1e-9999", "0o17", "0x2a"):
        expect_error(policy(f"symbol: {numeric}"),
                     "'symbol' must be a string scalar")
    for plain_string in ("inf", "Inf", "NAN", "+0o7", "-0x10"):
        expect_ok(policy(f"symbol: {plain_string}"))
    expect_error(
        '{"schema_version":1,"rules":[{"id":"json-typed",'
        '"action":"deny_add","match":{"symbol":42}}]}',
        "'symbol' must be a string scalar")
    expect_error(
        '{"schema_version":1,"rules":[{"id":true,'
        '"action":"deny_add","match":{"file_kind":"header"}}]}',
        "'id' must be a string scalar")
    expect_error(
        """schema_version: 1
rules:
  - id: null
    action: deny_add
    match: { file_kind: header }
""", "'id' must be a string scalar")
    expect_ok(
        """schema_version: 1
rules:
  - id: "null"
    action: deny_add
    match: { symbol: "42" }
""")
    expect_error(
        'schema_version: 1\nrules:\n  - id: decoded-nul\n'
        '    action: deny_add\n    match: { symbol: "before\\0after" }\n',
        "'symbol' contains an embedded NUL")

    # Version, enums, IDs and precompiled patterns are validated before use.
    expect_error("schema_version: 4\nrules: []\n", "unsupported schema_version '4'")
    expect_error('schema_version: "1"\nrules: []\n',
                 "'schema_version' must be an integer")
    expect_error(policy(action="unknown"), "unknown action 'unknown'")
    expect_error(policy("file_kind: object"), "unknown file_kind 'object'")
    expect_error(policy(rule_id="9invalid"), "invalid rule id '9invalid'")
    expect_error(policy(rule_id="éinvalid"), "invalid rule id 'éinvalid'")
    expect_error(
        """schema_version: 1
rules:
  - id: repeated
    action: deny_add
    match: { file_kind: header }
  - id: repeated
    action: deny_add
    match: { file_kind: source }
""", "duplicate rule id 'repeated'")
    expect_error(policy("includer: { regex: '[unterminated' }"),
                 "invalid regex")
    expect_error(policy("includee: { glob: '[unterminated' }"),
                 "invalid glob")
    expect_error(policy("includee: { glob: 'trailing\\' }"),
                 "trailing unescaped backslash")

    # YAML graph features and framing are deliberately outside this data format.
    expect_error(
        """schema_version: 1
rules:
  - &anchored
    id: anchored
    action: deny_add
    match: { file_kind: header }
""", "anchors are not allowed")
    expect_error(
        """schema_version: *missing
rules: []
""", "aliases are not allowed")
    expect_error(
        """schema_version: !!int 1
rules: []
""", "explicit tags are not allowed")
    expect_error(
        """schema_version: 1
rules: []
---
schema_version: 1
rules: []
""", "exactly one YAML document")
    expect_error("schema_version: 1\nrules: []\n---\n",
                 "exactly one YAML document")
    expect_error(b"schema_version: 1\nrules: []\x00\n", "embedded NUL")

    # Recognized future actions and unavailable facts fail as capabilities,
    # rather than being ignored or misreported as syntax errors.
    for action in ("associate", "export", "prefer_provider", "report_only"):
        expect_error(policy(action=action),
                     f"action '{action}' is recognized but not enabled")
    expect_ok(policy("active_condition: 'defined(FEATURE)'"))

    active_result = with_policy_for_source(
        policy(
            "includee: { glob: '*tests/cxx/indirect.h' }\n"
            "      active_condition: ENABLE_NATIVE_JSON_CONDITION",
            rule_id="active-condition-exact"),
        "tests/native_json/condition_add.cc")
    assert active_result.returncode == 0, active_result.stderr
    active_records = [json.loads(line)
                      for line in active_result.stdout.splitlines()]
    active_add = next(
        operation for record in active_records
        for operation in record["operations"]
        if operation["subject_action"] == "add" and
        "tests/cxx/indirect.h" in operation["spelling"])
    assert active_add["reason_code"] == "project_policy_deny_add"
    expect_error(policy("configuration: fastdebug"),
                 "requires --configuration")

    # File and option errors are fatal before analysis and never frame JSON.
    missing = invoke(ROOT / "tests" / "does-not-exist-policy.yaml")
    assert missing.returncode != 0 and missing.stdout == ""
    assert "cannot open project policy" in missing.stderr.lower()
    repeated = with_policy("schema_version: 1\nrules: []\n",
                           "--project_policy=second.yaml")
    assert repeated.returncode != 0 and repeated.stdout == ""
    assert "repeated --project_policy" in repeated.stderr

    text_error = with_policy("schema_version: 1\nrules: []\nextra: true\n",
                             json_output=False)
    assert text_error.returncode != 0 and text_error.stdout == ""
    assert "unknown top-level key 'extra'" in text_error.stderr

    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        directory = Path(directory)
        real = directory / "canonical.yaml"
        alias = directory / "alias.yaml"
        real.write_text("schema_version: 1\nrules: []\nextra: true\n")
        alias.symlink_to(real.name)
        canonical = invoke(alias)
        assert canonical.returncode != 0 and canonical.stdout == ""
        assert str(real.resolve()) in canonical.stderr
        assert str(alias) not in canonical.stderr


if __name__ == "__main__":
    main()
