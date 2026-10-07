#!/usr/bin/env python3
"""Opaque contracts for iwyu_tool.py schema-v1 JSON Lines mode."""

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

from validate_output_schema import semantic_errors


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "iwyu_tool.py"
IWYU = Path(sys.argv[1]).resolve()
SCHEMA = json.loads((ROOT / "docs/output-v1.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def validate_stream(output):
    assert output.endswith("\n"), "JSON Lines output lacks final newline"
    records = []
    for line in output.splitlines():
        assert line.strip(), "structured stream contains an empty record"
        record = json.loads(line)
        VALIDATOR.validate(record)
        errors = semantic_errors(record)
        assert not errors, errors
        records.append(record)
    assert records
    return records


def run_driver(database_dir, executable, extra_env=None):
    env = os.environ.copy()
    env["IWYU_BINARY"] = str(executable)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(DRIVER), "-o", "json", "-j", "2",
         "-p", str(database_dir)],
        cwd=ROOT, env=env, text=True, capture_output=True)


def write_database(directory, entries):
    (directory / "compile_commands.json").write_text(json.dumps(entries))


def write_fake_iwyu(path):
    path.write_text(r'''#!/usr/bin/env python3
import json
import os
import sys
import time

values = {}
args = sys.argv[1:]
for index, argument in enumerate(args):
    if argument == "-Xiwyu" and index + 1 < len(args):
        option = args[index + 1]
        if option.startswith("--") and "=" in option:
            name, value = option[2:].split("=", 1)
            values[name] = value
invocation_index = int(values["invocation_index"])
if invocation_index == 0:
    time.sleep(0.10)
print("native-stderr-%d" % invocation_index, file=sys.stderr)
if os.environ.get("FAKE_MALFORMED_INDEX") == str(invocation_index):
    print("malformed native stdout")
    sys.exit(0)
record = {
    "schema_version": 1,
    "record_type": "file_analysis",
    "tool": {
        "name": "include-what-you-use",
        "version": "fake-1",
        "git_revision": {"status": "unavailable", "value": None},
        "clang_version": "fake-clang-1",
    },
    "invocation": {
        "id": "inv-%06d-%s" % (
            invocation_index, values["command_digest"][7:19]),
        "index": invocation_index,
        "translation_unit": values["translation_unit"],
        "configuration": {"status": "unavailable", "value": None},
        "command_digest": values["command_digest"],
    },
    "file": {"path": values["translation_unit"], "kind": "source"},
    "status": "correct",
    "associated_headers": [],
    "analyzed_configurations": {"status": "unavailable", "value": None},
    "operations": [],
    "desired_state": {"status": "known", "complete": True, "items": []},
    "diagnostics": [],
}
print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
''')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def main():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        source = (ROOT / "tests/native_json/clean.cc").resolve()
        entry = {
            "directory": str(directory),
            "file": str(source),
            "arguments": ["clang++", "-std=c++17", str(source)],
        }
        write_database(directory, [entry, entry])
        real = run_driver(directory, IWYU)
        assert real.returncode == 0, real.stderr
        assert real.stderr == ""
        records = validate_stream(real.stdout)
        assert [record["invocation"]["index"] for record in records] == [0, 1]
        assert (records[0]["invocation"]["command_digest"] ==
                records[1]["invocation"]["command_digest"])
        assert (records[0]["invocation"]["id"] !=
                records[1]["invocation"]["id"])

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        first = directory / "first.cc"
        second = directory / "second.cc"
        first.write_text("int first;\n")
        second.write_text("int second;\n")
        entries = [
            {"directory": str(directory), "file": str(first),
             "arguments": ["clang++", str(first)]},
            {"directory": str(directory), "file": str(second),
             "arguments": ["clang++", str(second)]},
        ]
        write_database(directory, entries)
        fake = directory / "fake-iwyu.py"
        write_fake_iwyu(fake)

        ordered = run_driver(directory, fake)
        assert ordered.returncode == 0, ordered.stderr
        records = validate_stream(ordered.stdout)
        assert [record["invocation"]["index"] for record in records] == [0, 1]
        assert ordered.stderr == "native-stderr-0\nnative-stderr-1\n"

        malformed = run_driver(
            directory, fake, {"FAKE_MALFORMED_INDEX": "1"})
        assert malformed.returncode != 0
        assert malformed.stdout == ""
        assert "database index 1" in malformed.stderr
        assert "invalid JSON" in malformed.stderr
        assert "malformed native stdout" not in malformed.stderr
        assert "native-stderr-0\nnative-stderr-1\n" in malformed.stderr


if __name__ == "__main__":
    main()
