#!/usr/bin/env python3
"""Opaque contracts for iwyu_tool.py named compilation databases."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "iwyu_tool.py"


def write_database(path, entries):
    path.mkdir(parents=True, exist_ok=True)
    (path / "compile_commands.json").write_text(json.dumps(entries))


def write_fake_iwyu(path):
    path.write_text(r'''#!/usr/bin/env python3
import json
import os
import sys
import time

values = {}
args = sys.argv[1:]
for index, argument in enumerate(args[:-1]):
    if argument == "-Xiwyu":
        option = args[index + 1]
        if option.startswith("--") and "=" in option:
            name, value = option[2:].split("=", 1)
            values.setdefault(name, []).append(value)

for owned in ("output_format", "invocation_index", "command_digest",
              "translation_unit"):
    if len(values.get(owned, [])) != 1:
        print("bad protocol option %s" % owned, file=sys.stderr)
        sys.exit(90)
configurations = values.get("configuration", [])
if len(configurations) > 1:
    print("duplicate configuration", file=sys.stderr)
    sys.exit(91)

invocation_index = int(values["invocation_index"][0])
configuration = configurations[0] if configurations else None
# Make later entries finish first. The driver must still emit database/entry order.
time.sleep(0.03 * (3 - min(invocation_index, 3)))
print("diagnostic-%s-%d" % (configuration or "legacy", invocation_index),
      file=sys.stderr)

if (configuration is not None and
        os.environ.get("FAKE_MALFORMED_CONFIGURATION") == configuration):
    print("not-json")
    sys.exit(0)

reported_configuration = configuration
if (configuration is not None and
        os.environ.get("FAKE_MISMATCH_CONFIGURATION") == configuration):
    reported_configuration = "mismatch"

configuration_value = ({"status": "known", "value": reported_configuration}
                       if configuration is not None else
                       {"status": "unavailable", "value": None})
analyzed_value = ({"status": "known", "value": [reported_configuration]}
                  if configuration is not None else
                  {"status": "unavailable", "value": None})
digest = values["command_digest"][0]
translation_unit = values["translation_unit"][0]
record = {
    "schema_version": 1,
    "record_type": "file_analysis",
    "invocation": {
        "id": "inv-%06d-%s" % (invocation_index, digest[7:19]),
        "index": invocation_index,
        "translation_unit": translation_unit,
        "configuration": configuration_value,
        "command_digest": digest,
    },
    "file": {"path": translation_unit},
    "status": "correct",
    "analyzed_configurations": analyzed_value,
    "operations": [],
}
print(json.dumps(record, separators=(",", ":")))
''')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run(arguments, fake, extra_env=None):
    env = os.environ.copy()
    env["IWYU_BINARY"] = str(fake)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(DRIVER), "-o", "json", "-j", "3"] + arguments,
        cwd=ROOT, env=env, text=True, capture_output=True)


def records(result):
    return [json.loads(line) for line in result.stdout.splitlines()]


def require_failure(result, text):
    assert result.returncode != 0, result
    assert result.stdout == "", result.stdout
    assert text in result.stderr, result.stderr


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fake = root / "fake-iwyu.py"
        write_fake_iwyu(fake)

        common = root / "common.cc"
        server_only = root / "server.cc"
        common.write_text("int common;\n")
        server_only.write_text("int server;\n")
        common_entry = {
            "directory": str(root),
            "file": str(common),
            "arguments": ["clang++", "-c", str(common)],
        }
        server_entry = {
            "directory": str(root),
            "file": str(server_only),
            "arguments": ["clang++", "-c", str(server_only)],
        }
        server_db = root / "server-db"
        zero_db = root / "zero-db"
        write_database(server_db, [common_entry, server_entry])
        write_database(zero_db, [common_entry])

        named_args = [
            "--database", "server=%s" % server_db,
            "--database", "zero=%s" % zero_db,
        ]
        ordered = run(named_args, fake)
        assert ordered.returncode == 0, ordered.stderr
        output = records(ordered)
        assert [r["invocation"]["index"] for r in output] == [0, 1, 2]
        assert [r["invocation"]["configuration"]["value"]
                for r in output] == ["server", "server", "zero"]
        assert [r["analyzed_configurations"]["value"]
                for r in output] == [["server"], ["server"], ["zero"]]
        # Both databases contain an identical local index-0 command. Global
        # indices make its IDs distinct without changing its command digest.
        assert output[0]["invocation"]["command_digest"] == \
               output[2]["invocation"]["command_digest"]
        assert output[0]["invocation"]["id"] != output[2]["invocation"]["id"]
        assert ordered.stderr == (
            "diagnostic-server-0\n"
            "diagnostic-server-1\n"
            "diagnostic-zero-2\n")

        mismatch = run(
            named_args, fake, {"FAKE_MISMATCH_CONFIGURATION": "zero"})
        require_failure(mismatch, "configuration does not match named database")
        assert "diagnostic-server-0\n" in mismatch.stderr
        assert "diagnostic-zero-2\n" in mismatch.stderr

        malformed_child = run(
            named_args, fake, {"FAKE_MALFORMED_CONFIGURATION": "zero"})
        require_failure(malformed_child, "invalid JSON")
        assert "database 'zero' entry index 0" in malformed_child.stderr

        duplicate_name = run([
            "--database", "same=%s" % server_db,
            "--database", "same=%s" % zero_db], fake)
        require_failure(duplicate_name, "duplicate named database 'same'")

        alias = root / "server-alias.json"
        alias.symlink_to(server_db / "compile_commands.json")
        duplicate_path = run([
            "--database", "first=%s" % server_db,
            "--database", "second=%s" % alias], fake)
        require_failure(duplicate_path, "alias path")

        missing = run([
            "--database", "valid=%s" % server_db,
            "--database", "missing=%s" % (root / "missing")], fake)
        require_failure(missing, "failed to parse compilation database")
        assert "diagnostic-" not in missing.stderr

        empty_db = root / "empty-db"
        write_database(empty_db, [])
        empty = run([
            "--database", "valid=%s" % server_db,
            "--database", "empty=%s" % empty_db], fake)
        require_failure(empty, "is empty")
        assert "diagnostic-" not in empty.stderr

        malformed_db = root / "malformed-db"
        malformed_db.mkdir()
        (malformed_db / "compile_commands.json").write_text("{")
        malformed = run([
            "--database", "valid=%s" % server_db,
            "--database", "malformed=%s" % malformed_db], fake)
        require_failure(malformed, "failed to parse compilation database")
        assert "diagnostic-" not in malformed.stderr

        for configuration_option in (
                "--configuration=caller", "--config=caller"):
            injected = run(named_args + [
                "--", "-Xiwyu", configuration_option], fake)
            require_failure(injected, "reserved IWYU driver argument")

        legacy = run(["-p", str(zero_db)], fake)
        assert legacy.returncode == 0, legacy.stderr
        legacy_records = records(legacy)
        assert len(legacy_records) == 1
        legacy_invocation = legacy_records[0]["invocation"]
        assert legacy_invocation["index"] == 0
        assert legacy_invocation["configuration"] == {
            "status": "unavailable", "value": None}
        assert legacy_records[0]["analyzed_configurations"] == {
            "status": "unavailable", "value": None}
        assert legacy.stderr == "diagnostic-legacy-0\n"


if __name__ == "__main__":
    main()
