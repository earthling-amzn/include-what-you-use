#!/usr/bin/env python3
"""Validate output-v1 JSON Lines examples and semantic invariants."""
import hashlib
import json
from pathlib import Path
import posixpath
import re
import sys

try:
    import jsonschema
except ImportError:
    print("jsonschema package unavailable; skipping output schema validation")
    sys.exit(77)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs" / "output-v1.schema.json"
EXAMPLES = ROOT / "tests" / "schema" / "output-v1"
SUBJECT_RANK = {"remove": 0, "add": 1, "keep": 2}
DISPOSITION_RANK = {"apply": 0, "keep": 1, "suppress": 2, "report": 3}
KIND_RANK = {"include": 0, "forward_declaration": 1}
ORIGIN_RANK = {"ast": 0, "macro": 1, "heuristic": 2, "unknown": 3}
REQUIREMENT_RANK = {"full": 0, "forward_declaration": 1, "none": 2, "unknown": 3}
RANGE_STATUS_RANK = {"known": 0, "unknown": 1, "unavailable": 2, "not_applicable": 3}
INCLUDE_RE = re.compile(
    r'^\s*#\s*include\s+(?P<token><[^>]+>|"[^"]+")'
    r'(?:\s*(?://.*|/\*.*\*/\s*))?$')
PROVIDER_SPELLING_RE = re.compile(r'^(?:<[^>]+>|"[^"]+")$')


def records(path):
    result = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            result.append((number, json.loads(line)))
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path}:{number}: invalid JSON: {exc}") from exc
    if not result:
        raise AssertionError(f"{path}: no JSON Lines records")
    return result


def range_key(value):
    status = value["status"]
    if status != "known":
        return (RANGE_STATUS_RANK[status], "", 0, 0, 0, 0)
    start = value["value"]["start"]
    end = value["value"]["end"]
    return (RANGE_STATUS_RANK[status], start["path"], start["line"],
            start["column"], end["line"], end["column"])


def condition_key(condition):
    rank = {"known": 0, "unknown": 1, "unavailable": 2,
            "not_applicable": 3}
    status = condition["status"]
    if status != "known":
        return (rank[status], "", "", 0, 0, False, "", ())
    value = condition["value"]
    location = value["location"]
    if location["status"] == "known":
        location_value = location["value"]
        path = location_value["path"]
        line = location_value["line"]
        column = location_value["column"]
    else:
        path, line, column = "", 0, 0
    return (rank[status], value["expression"], path, line, column,
            value["active_branch"], value.get("source_region_id", ""),
            tuple(value.get("source_region_path", ())))


def operation_key(op):
    return (SUBJECT_RANK[op["subject_action"]],
            DISPOSITION_RANK[op["disposition"]],
            KIND_RANK[op["item_kind"]], range_key(op["source_range"]),
            op["spelling"], op["reason_code"],
            condition_key(op["edit_condition"]))


def available_name(value, fallback):
    return value["value"] if value["status"] == "known" else fallback


def symbol_key(symbol):
    return (available_name(symbol["qualified_name"], symbol["name"]),
            symbol["name"])



def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def use_key(use):
    location = use["location"]
    condition = use["condition"]
    macro = use["macro"]
    return (location["path"], location["line"], location["column"],
            ORIGIN_RANK[use["origin"]], REQUIREMENT_RANK[use["requirement"]],
            condition["status"], canonical(condition),
            macro["status"], canonical(macro))


def is_normalized_path(path):
    if "\\" in path or "//" in path or path.startswith("./"):
        return False
    if path != "/" and path.endswith("/"):
        return False
    components = [part for part in path.split("/") if part]
    if any(part in (".", "..") for part in components):
        return False
    return posixpath.normpath(path) == path

def provider_key(provider):
    return (provider["path"], provider["spelling"])


def record_identity(record):
    invocation = record["invocation"]
    return (invocation["index"], invocation["id"],
            invocation["translation_unit"], record["file"]["path"])


def semantic_errors(record):
    file_path = record["file"]["path"]
    status = record["status"]
    operations = record["operations"]
    desired = record["desired_state"]
    diagnostics = record["diagnostics"]
    analyzed = record["analyzed_configurations"]
    invocation = record["invocation"]
    errors = []

    digest = invocation["command_digest"].split(":", 1)[1]
    expected_id = f"inv-{invocation['index']:06d}-{digest[:12]}"
    if invocation["id"] != expected_id:
        errors.append("invocation ID does not match index/command digest")
    if analyzed["status"] == "known":
        configs = analyzed["value"]
        if not configs or len(configs) != len(set(configs)):
            errors.append("analyzed configurations must be non-empty and unique")
        if configs != sorted(configs):
            errors.append("analyzed configurations are not sorted")
        selected_config = invocation["configuration"]
        if (selected_config["status"] == "known" and
                selected_config["value"] not in configs):
            errors.append("invocation configuration is outside analyzed configurations")

    applied = [op for op in operations
               if op["disposition"] == "apply" and
               op["subject_action"] in ("add", "remove")]
    if status == "correct" and applied:
        errors.append("correct record contains applied edit")
    if status == "changes" and not applied:
        errors.append("changes record contains no applied edit")
    if status in ("skipped", "error") and applied:
        errors.append("skipped/error record contains applied edit")
    if status == "skipped" and not diagnostics:
        errors.append("skipped record contains no diagnostic")
    if status == "error" and not any(d["severity"] == "error" for d in diagnostics):
        errors.append("error record contains no error diagnostic")
    if status in ("correct", "changes") and (
            desired["status"] != "known" or not desired["complete"]):
        errors.append("correct/changes desired state is not known and complete")

    ids = [op["id"] for op in operations]
    if len(ids) != len(set(ids)):
        errors.append("operation IDs are not unique within the record")
    if ids != [f"op-{n}" for n in range(1, len(ids) + 1)]:
        errors.append("operation IDs are not sequential in output order")
    if operations != sorted(operations, key=operation_key):
        errors.append("operations are not deterministically sorted")
    if record["schema_version"] == 2:
        op_identities = [
            (op["subject_action"], op["item_kind"], op["spelling"],
             canonical(op["source_range"]), canonical(op["edit_condition"]))
            for op in operations]
    else:
        op_identities = [
            (op["subject_action"], op["item_kind"], op["spelling"],
             canonical(op["source_range"]))
            for op in operations]
    if len(op_identities) != len(set(op_identities)):
        errors.append("duplicate semantic operation identity")

    if record["schema_version"] == 2:
        present_occurrences = {}
        for op in operations:
            if op["subject_action"] not in ("remove", "keep"):
                continue
            occurrence = (
                op["item_kind"], op["spelling"],
                canonical(op["source_range"]))
            if op["source_range"]["status"] != "known":
                occurrence += (canonical(op["edit_condition"]),)
            previous = present_occurrences.get(occurrence)
            if previous is not None:
                errors.append(
                    f"{previous} and {op['id']}: ambiguous same present occurrence")
            present_occurrences[occurrence] = op["id"]

    associations = record["associated_headers"]
    association_key = lambda a: (a["path"], a["relationship"], a["reason_code"])
    association_keys = [association_key(a) for a in associations]
    if len(association_keys) != len(set(association_keys)):
        errors.append("duplicate associated-header relationship")
    if associations != sorted(associations, key=association_key):
        errors.append("associated headers are not deterministically sorted")

    allowed_dispositions = {
        "add": {"apply", "suppress", "report"},
        "remove": {"apply", "keep", "suppress", "report"},
        "keep": {"keep", "report"},
    }
    policy_disposition = {
        "allow": "apply", "deny": "suppress",
        "keep": "keep", "report": "report",
    }

    for op in operations:
        subject = op["subject_action"]
        disposition = op["disposition"]
        if disposition not in allowed_dispositions[subject]:
            errors.append(f"{op['id']}: invalid subject/disposition pair")

        source_range = op["source_range"]
        if subject == "add" and source_range["status"] != "not_applicable":
            errors.append(f"{op['id']}: add range must be not_applicable")
        if subject in ("remove", "keep") and source_range["status"] == "not_applicable":
            errors.append(f"{op['id']}: present item range cannot be not_applicable")
        if source_range["status"] == "known":
            start = source_range["value"]["start"]
            end = source_range["value"]["end"]
            if start["path"] != end["path"]:
                errors.append(f"{op['id']}: range endpoints use different files")
            if start["path"] != file_path:
                errors.append(f"{op['id']}: range is outside analyzed file")
            if (end["line"], end["column"]) < (start["line"], start["column"]):
                errors.append(f"{op['id']}: range end precedes start")

        symbols = op["symbols"]
        identities = [(s["name"], available_name(s["qualified_name"], s["name"]))
                      for s in symbols]
        if len(identities) != len(set(identities)):
            errors.append(f"{op['id']}: duplicate symbol identity")
        if symbols != sorted(symbols, key=symbol_key):
            errors.append(f"{op['id']}: symbols are not deterministically sorted")
        for symbol in symbols:
            use_identities = [use_key(use) for use in symbol["uses"]]
            if len(use_identities) != len(set(use_identities)):
                errors.append(f"{op['id']}: duplicate symbol use identity")
            if symbol["uses"] != sorted(symbol["uses"], key=use_key):
                errors.append(f"{op['id']}: symbol uses are not deterministically sorted")
            for use in symbol["uses"]:
                if use["location"]["path"] != file_path:
                    errors.append(f"{op['id']}: symbol use is outside analyzed file")
                macro = use["macro"]
                if use["origin"] == "macro" and macro["status"] == "not_applicable":
                    errors.append(f"{op['id']}: macro-origin use has non-applicable macro provenance")
                if use["origin"] != "macro" and macro["status"] == "known":
                    errors.append(f"{op['id']}: known macro provenance has non-macro origin")
                if macro["status"] == "known":
                    expansion = macro["value"]["expansion"]
                    if (expansion["status"] == "known" and
                            expansion["value"] != use["location"]):
                        errors.append(f"{op['id']}: macro expansion does not match use location")
                condition = use["condition"]
                if (condition["status"] == "known" and
                        condition["value"]["location"]["status"] == "known" and
                        condition["value"]["location"]["value"]["path"] != file_path):
                    errors.append(f"{op['id']}: use condition is outside analyzed file")

        provider = op["provider"]
        selected = provider["selected"]
        candidates = provider["candidates"]
        chain = provider["chain"]
        all_provider_refs = []
        if selected["status"] == "known":
            all_provider_refs.append(selected["value"])
        if candidates["status"] == "known":
            all_provider_refs.extend(candidates["value"])
        for ref in all_provider_refs:
            if not PROVIDER_SPELLING_RE.match(ref["spelling"]):
                errors.append(f"{op['id']}: invalid provider include spelling")
            if not is_normalized_path(ref["path"]):
                errors.append(f"{op['id']}: provider path is not normalized")
        if chain["status"] == "known" and any(
                not is_normalized_path(path) for path in chain["value"]):
            errors.append(f"{op['id']}: provider chain path is not normalized")
        if candidates["status"] == "known":
            values = candidates["value"]
            keys = [provider_key(value) for value in values]
            if len(keys) != len(set(keys)):
                errors.append(f"{op['id']}: duplicate provider candidate")
            if values != sorted(values, key=provider_key):
                errors.append(f"{op['id']}: provider candidates are not sorted")
            if selected["status"] == "known" and selected["value"] not in values:
                errors.append(f"{op['id']}: selected provider is absent from candidates")
        if selected["status"] == "known" and op["item_kind"] == "include":
            match = INCLUDE_RE.match(op["spelling"])
            if not match or match.group("token") != selected["value"]["spelling"]:
                errors.append(f"{op['id']}: selected provider spelling contradicts include operation")
        if chain["status"] == "known":
            if selected["status"] != "known":
                errors.append(f"{op['id']}: known provider chain has no selected provider")
            elif (not chain["value"] or chain["value"][0] != file_path or
                  chain["value"][-1] != selected["value"]["path"]):
                errors.append(f"{op['id']}: provider chain endpoints are inconsistent")

        edit_condition = op["edit_condition"]
        if (edit_condition["status"] == "known" and
                edit_condition["value"]["location"]["status"] == "known" and
                edit_condition["value"]["location"]["value"]["path"] != file_path):
            errors.append(f"{op['id']}: edit condition is outside analyzed file")

        policy = op["policy"]
        if policy["status"] == "known":
            decision = policy["value"]["decision"]
            if policy_disposition[decision] != disposition:
                errors.append(f"{op['id']}: policy decision contradicts disposition")

        multi = op["multi_config"]
        if (analyzed["status"] == "known" and len(analyzed["value"]) > 1 and
                multi["status"] != "known"):
            errors.append(f"{op['id']}: multi-config aggregate lacks full contributions")
        if multi["status"] == "known":
            contributions = multi["value"]
            configs = [item["configuration"] for item in contributions]
            if len(configs) != len(set(configs)):
                errors.append(f"{op['id']}: duplicate multi-config configuration")
            if configs != sorted(configs):
                errors.append(f"{op['id']}: multi-config contributions are not sorted")
            if analyzed["status"] != "known" or configs != analyzed["value"]:
                errors.append(f"{op['id']}: multi-config coverage differs from analyzed configurations")
            for item in contributions:
                none = item["subject_action"] == "none"
                contribution_condition = item["condition"]
                if (contribution_condition["status"] == "known" and
                        contribution_condition["value"]["location"]["status"] == "known" and
                        contribution_condition["value"]["location"]["value"]["path"] != file_path):
                    errors.append(f"{op['id']}: contribution condition is outside analyzed file")
                if none != (item["disposition"] == "not_applicable"):
                    errors.append(f"{op['id']}: no-recommendation contribution is inconsistent")
                if not none and item["subject_action"] != subject:
                    errors.append(f"{op['id']}: multi-config subject contradicts operation")
                if none and contribution_condition["status"] != "not_applicable":
                    errors.append(f"{op['id']}: no-recommendation condition must be not_applicable")
                if not none and contribution_condition != op["edit_condition"]:
                    errors.append(f"{op['id']}: contribution condition contradicts edit condition")
            if disposition in ("apply", "keep", "suppress") and any(
                    item["subject_action"] != subject or
                    item["disposition"] != disposition for item in contributions):
                errors.append(f"{op['id']}: multi-config disposition contradicts operation")
            if disposition == "report" and not any(
                    item["disposition"] == "report" for item in contributions):
                errors.append(f"{op['id']}: reported aggregate has no report contribution")

    if desired["status"] == "known":
        items = desired["items"]
        keys = [(item["item_kind"], item["spelling"]) for item in items]
        if len(keys) != len(set(keys)):
            errors.append("duplicate desired-state item")
        desired_spellings = set(keys)
        if record["schema_version"] == 1:
            for op in operations:
                key = (op["item_kind"], op["spelling"])
                should_be_present = (
                    (op["subject_action"] == "add" and
                     op["disposition"] == "apply") or
                    (op["subject_action"] == "remove" and
                     op["disposition"] != "apply") or
                    op["subject_action"] == "keep")
                if should_be_present and key not in desired_spellings:
                    errors.append(
                        f"{op['id']}: no-edit/applied-add item missing from desired state")
                if not should_be_present and key in desired_spellings:
                    errors.append(
                        f"{op['id']}: removed/suppressed-add item remains in desired state")
        else:
            operations_by_key = {}
            for op in operations:
                key = (op["item_kind"], op["spelling"])
                operations_by_key.setdefault(key, []).append(op)
            for key, key_operations in operations_by_key.items():
                presence_witnesses = [
                    op for op in key_operations
                    if ((op["subject_action"] == "add" and
                         op["disposition"] == "apply") or
                        (op["subject_action"] == "remove" and
                         op["disposition"] != "apply") or
                        op["subject_action"] == "keep")]
                if ((key in desired_spellings) !=
                        bool(presence_witnesses)):
                    ids = ", ".join(op["id"] for op in key_operations)
                    errors.append(
                        f"{ids}: operation group contradicts desired state")
    return errors


def validate_records(path, validator):
    loaded = records(path)
    failures = []
    valid_records = []
    for number, record in loaded:
        schema_errors = sorted(validator.iter_errors(record), key=lambda e: list(e.path))
        if schema_errors:
            failures.extend(
                f"{path.name}:{number}: schema: "
                f"{'.'.join(map(str, error.path)) or '<record>'}: {error.message}"
                for error in schema_errors)
            continue
        valid_records.append((number, record))
        failures.extend(f"{path.name}:{number}: semantic: {error}"
                        for error in semantic_errors(record))

    identities = [record_identity(record) for _, record in valid_records]
    if len(identities) != len(set(identities)):
        failures.append(f"{path.name}: semantic: duplicate record identity")
    if identities != sorted(identities):
        failures.append(f"{path.name}: semantic: records are not deterministically sorted")
    return loaded, failures


def validate_invocation_vectors():
    path = EXAMPLES / "invocation-id-vectors.json"
    for number, vector in enumerate(json.loads(path.read_text()), 1):
        command = vector["command"]
        ordered = {
            "directory": command["directory"],
            "file": command["file"],
            "arguments": command["arguments"],
        }
        canonical = json.dumps(ordered, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        invocation_id = f"inv-{vector['index']:06d}-{digest[:12]}"
        if canonical != vector["canonical"]:
            raise AssertionError(f"invocation vector {number}: canonical bytes differ")
        if "sha256:" + digest != vector["command_digest"]:
            raise AssertionError(f"invocation vector {number}: digest differs")
        if invocation_id != vector["invocation_id"]:
            raise AssertionError(f"invocation vector {number}: ID differs")


def main():
    validate_invocation_vectors()
    schema = json.loads(SCHEMA.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    v2_schema = json.loads((ROOT / "docs" / "output-v2.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(v2_schema)
    validator = jsonschema.Draft202012Validator(schema)

    positive = EXAMPLES / "positive.jsonl"
    positive_records, failures = validate_records(positive, validator)
    if failures:
        raise AssertionError(failures[0])

    expectations = json.loads((EXAMPLES / "negative-expectations.json").read_text())
    negatives = sorted(EXAMPLES.glob("negative-*.jsonl"))
    if set(expectations) != {path.name for path in negatives}:
        raise AssertionError("negative expectation list does not match fixtures")
    for path in negatives:
        _, negative_failures = validate_records(path, validator)
        if not negative_failures:
            raise AssertionError(f"{path}: negative fixture unexpectedly validates")
        combined = "\n".join(negative_failures)
        expected = expectations[path.name]
        if expected not in combined:
            raise AssertionError(
                f"{path}: expected failure containing {expected!r}, got:\n{combined}")

    print(f"PASS: {len(positive_records)} positive records, "
          f"{len(negatives)} targeted negative fixtures")
    return 0


if __name__ == "__main__":
    sys.exit(main())