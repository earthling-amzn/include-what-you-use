#!/usr/bin/env python3
"""Opaque generic SEM-004 definition-availability contracts."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "inline_definition_availability"
IWYU = Path(sys.argv[1]).resolve()
CXX = Path(sys.argv[2]).resolve()
DRIVER = ROOT / "iwyu_tool.py"


def run(command, cwd=CORPUS, stdin=None, expect=0):
    result = subprocess.run([str(arg) for arg in command], cwd=cwd,
                            input=stdin, text=True, capture_output=True)
    assert result.returncode == expect, (
        f"command returned {result.returncode}, expected {expect}: {command}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def analyze(filename, *options, corpus=CORPUS, schema=1):
    command = [IWYU, "-Xiwyu", "--output_format=json"]
    if schema != 1:
        command += ["-Xiwyu", f"--output_schema={schema}"]
    command += ["-I", ".", "-std=c++17", *options, filename]
    first = run(command, cwd=corpus)
    second = run(command, cwd=corpus)
    assert second.stdout == first.stdout, filename
    records = [json.loads(line) for line in first.stdout.splitlines()]
    return next(record for record in records
                if record["file"]["path"] == filename), records


def definition_diagnostics(record):
    return [fact for fact in record["diagnostics"]
            if fact["code"] in {"missing_inline_definition",
                                "missing_template_definition",
                                "definition_provider_unproven"}]


def analyze_with_driver(entries, corpus=CORPUS):
    """Run real schema-v2 driver records for (filename, IWYU options)."""
    with tempfile.TemporaryDirectory(prefix="sem004-driver-",
                                     dir=ROOT / "build") as temporary:
        database = Path(temporary)
        commands = []
        for filename, options in entries:
            source = (corpus / filename).resolve()
            commands.append({
                "directory": str(corpus.resolve()),
                "file": str(source),
                "arguments": [str(CXX), "-I", ".", "-std=c++17",
                              *options, "-c", str(source)],
            })
        (database / "compile_commands.json").write_text(json.dumps(commands))
        environment = os.environ.copy()
        environment["IWYU_BINARY"] = str(IWYU)
        result = subprocess.run(
            [sys.executable, str(DRIVER), "-o", "json",
             "--output-schema", "2", "-j", "2", "-p", str(database)],
            cwd=ROOT, env=environment, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert result.stderr == "", result.stderr
        records = [json.loads(line) for line in result.stdout.splitlines()]
        assert records
        assert all(record["schema_version"] == 2 for record in records)
        return records


def semantic_keeps(record):
    return [op for op in record["operations"]
            if op["reason_code"] == "semantic_definition_provider"]


def assert_provider_keep(operation, spelling, chain):
    assert (operation["subject_action"], operation["disposition"],
            operation["spelling"]) == ("keep", "keep", spelling)
    assert operation["source_range"]["status"] == "known"
    selected = operation["provider"]["selected"]
    assert selected["status"] == "known"
    include_token = spelling.split()[1]
    assert selected["value"]["spelling"] == include_token
    assert operation["provider"]["chain"] == {
        "status": "known", "value": chain}
    assert chain[0] == operation["source_range"]["value"]["start"]["path"]
    assert chain[-1] == selected["value"]["path"]
    assert operation["symbols"]
    identities = [(symbol["name"], symbol["qualified_name"]["value"])
                  for symbol in operation["symbols"]]
    assert len(identities) == len(set(identities)), identities
    for symbol in operation["symbols"]:
        assert symbol["qualified_name"]["status"] == "known"
        assert symbol["declaration"]["status"] == "known"
        assert symbol["uses"]
        assert all(use["origin"] == "ast" and
                   use["requirement"] == "full" and
                   use["location"]["path"] == chain[0]
                   for use in symbol["uses"]), symbol


def assert_analyzer_contracts():
    available, _ = analyze("available.cc")
    assert available["status"] == "correct"
    assert available["diagnostics"] == []
    assert_provider_keep(semantic_keeps(available)[0],
                         '#include "api.inline.hpp"',
                         ["available.cc", "api.inline.hpp"])
    assert available["desired_state"]["items"] == [{
        "item_kind": "include",
        "spelling": '#include "api.inline.hpp"',
        "symbols": [
            "InlineAddress", "InlineFree", "InlineLifecycle",
            "InlineLifecycle::AddressMember",
            "InlineLifecycle::InlineLifecycle", "InlineLifecycle::Member",
            "InlineLifecycle::~InlineLifecycle", "InlineTemplate",
        ],
    }]

    missing, _ = analyze("missing.cc")
    assert not [op for op in missing["operations"]
                if op["subject_action"] == "add"]
    facts = [diagnostic for diagnostic in missing["diagnostics"]
             if diagnostic["code"] == "missing_inline_definition"]
    assert [fact["location"]["value"]["line"] for fact in facts] == [
        4, 4, 5, 6, 7, 7, 8]
    assert all("inline-definition provider" in fact["message"]
               for fact in facts)

    contexts, _ = analyze("contexts.cc")
    assert definition_diagnostics(contexts) == []
    pure_pointer, _ = analyze("pure_virtual_pointer.cc")
    assert definition_diagnostics(pure_pointer) == []
    pure_qualified, _ = analyze("pure_virtual_qualified.cc")
    assert [fact["code"] for fact in definition_diagnostics(pure_qualified)] == [
        "missing_inline_definition"]
    control, _ = analyze("control.cc")
    assert definition_diagnostics(control) == []
    assert [(op["subject_action"], op["disposition"], op["spelling"])
            for op in control["operations"]] == [
        ("remove", "apply", '#include "unused.inline.hpp"')]

    default_provider, _ = analyze("default_provider.cc")
    assert definition_diagnostics(default_provider) == []
    default_body = next(op for op in semantic_keeps(default_provider)
                        if op["spelling"] ==
                        '#include "default_provider.inline.hpp"')
    assert_provider_keep(default_body,
                         '#include "default_provider.inline.hpp"', [
                             "default_provider.cc",
                             "default_provider.inline.hpp"])
    assert [symbol["qualified_name"]["value"]
            for symbol in default_body["symbols"]] == ["MissingDefault"]
    assert {use["location"]["line"]
            for symbol in default_body["symbols"]
            for use in symbol["uses"]} == {3}

    default_missing, _ = analyze("default_provider_missing.cc")
    assert [(fact["code"], fact["location"]["value"]["line"])
            for fact in definition_diagnostics(default_missing)] == [
        ("missing_inline_definition", 2)]
    assert "MissingDefault" in definition_diagnostics(default_missing)[0][
        "message"]

    default_controls, _ = analyze("default_provider_controls.cc")
    assert definition_diagnostics(default_controls) == []
    assert not [op for op in semantic_keeps(default_controls)
                if op["spelling"] ==
                '#include "default_provider.inline.hpp"']

    instantiated_default, _ = analyze("default_provider_instantiated.cc")
    assert definition_diagnostics(instantiated_default) == []
    instantiated_body = next(
        op for op in semantic_keeps(instantiated_default)
        if op["spelling"] == '#include "default_provider.inline.hpp"')
    assert_provider_keep(instantiated_body,
                         '#include "default_provider.inline.hpp"', [
                             "default_provider_instantiated.cc",
                             "default_provider.inline.hpp"])
    assert [(symbol["qualified_name"]["value"],
             [(use["location"]["line"], use["location"]["column"])
              for use in symbol["uses"]])
            for symbol in instantiated_body["symbols"]] == [
        ("MissingDefault", [(4, 21)])]

    defaulted_provider, _ = analyze("defaulted_provider.cc")
    assert definition_diagnostics(defaulted_provider) == []
    lifecycle = next(op for op in semantic_keeps(defaulted_provider)
                     if op["spelling"] ==
                     '#include "defaulted_provider.inline.hpp"')
    assert_provider_keep(lifecycle,
                         '#include "defaulted_provider.inline.hpp"', [
                             "defaulted_provider.cc",
                             "defaulted_provider.inline.hpp"])
    lifecycle_names = {symbol["qualified_name"]["value"]
                       for symbol in lifecycle["symbols"]}
    assert lifecycle_names == {
        "LifecycleDependency::LifecycleDependency",
        "LifecycleDependency::operator=",
        "LifecycleDependency::~LifecycleDependency"}
    assert {use["location"]["line"]
            for symbol in lifecycle["symbols"]
            for use in symbol["uses"]} == {4, 5, 6, 7, 8}

    defaulted_trivial, _ = analyze("defaulted_trivial.cc")
    assert definition_diagnostics(defaulted_trivial) == []
    assert semantic_keeps(defaulted_trivial) == []

    pure_destructor, _ = analyze("pure_virtual_destructor.cc")
    assert [(fact["code"], fact["location"]["value"]["line"])
            for fact in definition_diagnostics(pure_destructor)] == [
        ("missing_inline_definition", 5)]
    assert "PureDestructorBase::~PureDestructorBase" in \
        definition_diagnostics(pure_destructor)[0]["message"]

    deep_parent, _ = analyze("deep_parent.cc")
    assert [(fact["code"], fact["location"]["value"]["line"])
            for fact in definition_diagnostics(deep_parent)] == [
        ("missing_inline_definition", 2)]

    late_same, _ = analyze("late_same.cc")
    assert definition_diagnostics(late_same) == []
    assert semantic_keeps(late_same) == []

    late_provider, _ = analyze("late_template_provider.cc")
    assert definition_diagnostics(late_provider) == []
    late_keep = semantic_keeps(late_provider)
    assert len(late_keep) == 1
    assert_provider_keep(late_keep[0],
                         '#include "late_template_provider.inline.hpp"', [
                             "late_template_provider.cc",
                             "late_template_provider.inline.hpp"])
    assert late_keep[0]["source_range"]["value"]["start"]["line"] == 3
    assert [symbol["qualified_name"]["value"]
            for symbol in late_keep[0]["symbols"]] == ["LateProvider"]

    uninstantiated, _ = analyze("uninstantiated_nondependent.cc")
    assert definition_diagnostics(uninstantiated) == []
    instantiated, _ = analyze("instantiated_nondependent.cc")
    assert [(fact["code"], fact["location"]["value"]["line"])
            for fact in definition_diagnostics(instantiated)] == [
        ("missing_inline_definition", 3)]

    template_available, _ = analyze("template_available.cc")
    assert definition_diagnostics(template_available) == []
    keep = semantic_keeps(template_available)
    assert len(keep) == 1
    assert_provider_keep(keep[0], '#include "template_bodies.hpp"', [
        "template_available.cc", "template_bodies.hpp"])
    qualified = {symbol["qualified_name"]["value"]
                 for symbol in keep[0]["symbols"]}

    duplicate_calls, _ = analyze("duplicate_calls.cc")
    duplicate_keep = semantic_keeps(duplicate_calls)
    assert len(duplicate_keep) == 1
    assert_provider_keep(duplicate_keep[0], '#include "template_bodies.hpp"',
                         ["duplicate_calls.cc", "template_bodies.hpp"])
    assert len(duplicate_keep[0]["symbols"]) == 1
    assert len(duplicate_keep[0]["symbols"][0]["uses"]) == 2
    assert {"TemplateFunction", "TemplateBox<int>::Member",
            "TemplateBox<int>::AddressMember",
            "TemplateBox<int>::MemberTemplate"} <= qualified

    template_missing, _ = analyze("template_missing.cc")
    missing_templates = [fact for fact in definition_diagnostics(template_missing)
                         if fact["code"] == "missing_template_definition"]
    names = {fact["message"].split("'")[1] for fact in missing_templates}
    assert {"TemplateFunction", "TemplateBox<int>::TemplateBox",
            "TemplateBox<int>::~TemplateBox", "TemplateBox<int>::Member",
            "TemplateBox<int>::AddressMember",
            "TemplateBox<int>::MemberTemplate"} <= names
    assert not [op for op in template_missing["operations"]
                if op["subject_action"] == "add"]

    template_contexts, _ = analyze("template_contexts.cc")
    assert definition_diagnostics(template_contexts) == []

    extern_consumer, _ = analyze("extern_consumer.cc")
    assert definition_diagnostics(extern_consumer) == []
    assert semantic_keeps(extern_consumer) == []
    late_extern, _ = analyze("late_extern.cc")
    assert [fact["code"] for fact in definition_diagnostics(late_extern)] == [
        "missing_template_definition"]
    assert definition_diagnostics(late_extern)[0]["location"]["value"]["line"] == 3
    for feature in (0, 1):
        conditional_extern, _ = analyze(
            "conditional_extern.cc", f"-DFEATURE={feature}")
        assert [fact["code"] for fact in
                definition_diagnostics(conditional_extern)] == [
            "missing_template_definition"]
    instantiations, _ = analyze("extern_instantiations.cc")
    assert definition_diagnostics(instantiations) == []
    instantiation_keep = semantic_keeps(instantiations)[0]
    assert_provider_keep(instantiation_keep,
                         '#include "template_bodies.hpp"', [
                             "extern_instantiations.cc",
                             "template_bodies.hpp"])
    instantiation_names = {symbol["qualified_name"]["value"]
                           for symbol in instantiation_keep["symbols"]}
    assert "TemplateFunction" in instantiation_names
    assert "TemplateBox<int>::TemplateBox" in instantiation_names
    assert all(use["location"]["line"] <= 4
               for symbol in instantiation_keep["symbols"]
               for use in symbol["uses"])

    explicit, _ = analyze("explicit_instantiations.cc", schema=2)
    assert definition_diagnostics(explicit) == []
    explicit_keeps = semantic_keeps(explicit)
    assert len(explicit_keeps) == 1
    assert_provider_keep(explicit_keeps[0],
                         '#include "explicit_api.inline.hpp"', [
                             "explicit_instantiations.cc",
                             "explicit_api.inline.hpp"])
    assert {symbol["qualified_name"]["value"]
            for symbol in explicit_keeps[0]["symbols"]} == {
        "Explicit", "ExplicitClassBox<int>::Member",
        "ExplicitMemberBox<int>::Member"}
    assert {use["location"]["line"]
            for symbol in explicit_keeps[0]["symbols"]
            for use in symbol["uses"]} == {1}

    body_first, _ = analyze("explicit_body_first.cc", schema=2)
    assert definition_diagnostics(body_first) == []
    assert_provider_keep(semantic_keeps(body_first)[0],
                         '#include "explicit_body_first.inline.hpp"', [
                             "explicit_body_first.cc",
                             "explicit_body_first.inline.hpp"])

    explicit_export, _ = analyze("explicit_export.cc", schema=2)
    assert definition_diagnostics(explicit_export) == []
    assert_provider_keep(semantic_keeps(explicit_export)[0],
                         '#include "explicit_export_carrier.hpp"', [
                             "explicit_export.cc",
                             "explicit_export_carrier.hpp"])
    explicit_mapped, _ = analyze(
        "explicit_mapped.cc", "-Xiwyu",
        "--mapping_file=explicit_providers.imp", schema=2)
    assert definition_diagnostics(explicit_mapped) == []
    assert_provider_keep(semantic_keeps(explicit_mapped)[0],
                         '#include "explicit_mapped_carrier.hpp"', [
                             "explicit_mapped.cc",
                             "explicit_mapped_carrier.hpp"])

    explicit_conditional, _ = analyze(
        "explicit_conditional.cc", "-DFEATURE=1", schema=2)
    assert definition_diagnostics(explicit_conditional) == []
    conditional_keep = next(
        operation for operation in semantic_keeps(explicit_conditional)
        if operation["spelling"] == '#include "explicit_api.inline.hpp"')
    assert_provider_keep(conditional_keep,
                         '#include "explicit_api.inline.hpp"', [
                             "explicit_conditional.cc",
                             "explicit_api.inline.hpp"])
    assert conditional_keep["edit_condition"]["status"] == "known"
    explicit_conditional_inactive, _ = analyze(
        "explicit_conditional.cc", "-DFEATURE=0", schema=2)
    assert semantic_keeps(explicit_conditional_inactive) == []
    assert definition_diagnostics(explicit_conditional_inactive) == []

    explicit_declaration, _ = analyze(
        "explicit_declaration_only.cc", schema=2)
    assert semantic_keeps(explicit_declaration) == []

    ambiguity_cases = {
        "ambiguity_free": {
            '#include "ambiguity_free_two_carrier.hpp"':
                "AmbiguousClash",
            '#include "ambiguity_free_one_carrier.hpp"':
                "AmbiguousClash",
        },
        "ambiguity_member": {
            '#include "ambiguity_member_two_carrier.hpp"':
                "AmbiguousMemberBox<int>::Second",
            '#include "ambiguity_member_one_carrier.hpp"':
                "AmbiguousMemberBox<int>::First",
        },
        "ambiguity_class": {
            '#include "ambiguity_class_two_carrier.hpp"':
                "AmbiguousTwo::Box<int>::Get",
            ('#include "ambiguity_class_one_carrier.hpp"  '
             '// IWYU pragma: associated'):
                "AmbiguousOne::Box<int>::Get",
        },
    }
    for stem, expected_roots in ambiguity_cases.items():
        active, _ = analyze(
            f"{stem}.cc", "-DFEATURE=1", "-Xiwyu",
            f"--mapping_file={stem}.imp", schema=2)
        assert definition_diagnostics(active) == []
        keeps = semantic_keeps(active)
        assert {operation["spelling"] for operation in keeps} == \
            set(expected_roots)
        for operation in keeps:
            spelling = operation["spelling"]
            assert_provider_keep(operation, spelling, [
                f"{stem}.cc",
                spelling.split('"')[1],
            ])
            assert [symbol["qualified_name"]["value"]
                    for symbol in operation["symbols"]] == [
                        expected_roots[spelling]]
            expected_line = 3 if "two_carrier" in spelling else 6
            assert {use["location"]["line"]
                    for symbol in operation["symbols"]
                    for use in symbol["uses"]} == {expected_line}
        duplicate = f'#include "{stem}_duplicate_carrier.hpp"'
        assert [(operation["subject_action"], operation["disposition"])
                for operation in active["operations"]
                if operation["spelling"] == duplicate] == [
            ("remove", "apply")]

        inactive, _ = analyze(
            f"{stem}.cc", "-DFEATURE=0", "-Xiwyu",
            f"--mapping_file={stem}.imp", schema=2)
        assert definition_diagnostics(inactive) == []
        inactive_keeps = semantic_keeps(inactive)
        expected_one = f'#include "{stem}_one_carrier.hpp"'
        if stem == "ambiguity_class":
            expected_one += "  // IWYU pragma: associated"
        assert [operation["spelling"] for operation in inactive_keeps] == [
            expected_one]
        assert all(use["location"]["path"] == f"{stem}.cc"
                   for operation in inactive_keeps
                   for symbol in operation["symbols"]
                   for use in symbol["uses"])

    explicit_same_file, _ = analyze("explicit_same_file.cc", schema=2)
    assert semantic_keeps(explicit_same_file) == []
    assert definition_diagnostics(explicit_same_file) == []
    assert definition_diagnostics(explicit_declaration) == []
    assert [(operation["subject_action"], operation["disposition"],
             operation["spelling"])
            for operation in explicit_declaration["operations"]
            if operation["spelling"] ==
               '#include "explicit_body_first.inline.hpp"'] == [
        ("remove", "apply",
         '#include "explicit_body_first.inline.hpp"')]

    later_matching, _ = analyze(
        "later_definition_matching.cc", "-DFEATURE=1", schema=2)
    assert definition_diagnostics(later_matching) == []
    later_matching_inactive, _ = analyze(
        "later_definition_matching.cc", "-DFEATURE=0", schema=2)
    assert definition_diagnostics(later_matching_inactive) == []
    later_mismatched, _ = analyze(
        "later_definition_mismatched.cc", "-DFEATURE=1", schema=2)
    assert [(fact["code"], fact["location"]["value"]["line"])
            for fact in definition_diagnostics(later_mismatched)] == [
        ("definition_provider_unproven", 4)]
    later_inactive, _ = analyze(
        "later_definition_mismatched.cc", "-DFEATURE=0", schema=2)
    assert [(fact["code"], fact["location"]["value"]["line"])
            for fact in definition_diagnostics(later_inactive)] == [
        ("missing_template_definition", 4)]
    later_sibling, _ = analyze(
        "later_definition_sibling.cc", "-DFEATURE=1", schema=2)
    assert [(fact["code"], fact["location"]["value"]["line"])
            for fact in definition_diagnostics(later_sibling)] == [
        ("missing_template_definition", 4)]

    direct_after, _ = analyze("direct_after.cc")
    assert definition_diagnostics(direct_after) == []
    body_keep = next(op for op in semantic_keeps(direct_after)
                     if op["spelling"] == '#include "api.inline.hpp"')
    assert_provider_keep(body_keep,
                         '#include "api.inline.hpp"',
                         ["direct_after.cc", "api.inline.hpp"])
    assert body_keep["source_range"]["value"]["start"]["line"] == 5

    dominating, _ = analyze("conditional_dominating.cc", "-DFEATURE=1")
    assert definition_diagnostics(dominating) == []
    dominating_body = next(
        op for op in semantic_keeps(dominating)
        if op["spelling"] == '#include "api.inline.hpp"')
    assert dominating_body["edit_condition"]["status"] == "known"
    inactive, _ = analyze("conditional_dominating.cc", "-DFEATURE=0")
    assert definition_diagnostics(inactive) == []

    nondominating, _ = analyze("conditional_nondominating.cc", "-DFEATURE=1")
    assert [fact["code"] for fact in definition_diagnostics(nondominating)] == [
        "definition_provider_unproven"]
    assert semantic_keeps(nondominating) == []
    nondominating_inactive, _ = analyze(
        "conditional_nondominating.cc", "-DFEATURE=0")
    assert [fact["code"] for fact in
            definition_diagnostics(nondominating_inactive)] == [
        "missing_inline_definition"]

    exported, _ = analyze("export_consumer.cc")
    assert definition_diagnostics(exported) == []
    assert_provider_keep(semantic_keeps(exported)[0],
                         '#include "export_carrier.hpp"', [
                             "export_consumer.cc", "export_carrier.hpp"])

    mapped, _ = analyze("mapped_consumer.cc", "-Xiwyu",
                        "--mapping_file=providers.imp")
    assert definition_diagnostics(mapped) == []
    assert_provider_keep(semantic_keeps(mapped)[0],
                         '#include "mapped_carrier.hpp"', [
                             "mapped_consumer.cc", "mapped_carrier.hpp"])

    repeated_zero, _ = analyze("repeated_provider.cc", "-DFEATURE=0")
    assert definition_diagnostics(repeated_zero) == []
    assert_provider_keep(semantic_keeps(repeated_zero)[0],
                         '#include "repeat_second.hpp"', [
                             "repeated_provider.cc", "repeat_second.hpp"])
    repeated_one, _ = analyze("repeated_provider.cc", "-DFEATURE=1")
    assert [fact["code"] for fact in definition_diagnostics(repeated_one)] == [
        "definition_provider_unproven"]
    assert semantic_keeps(repeated_one) == []

    associated, associated_records = analyze("associated_consumer.cc")
    assert definition_diagnostics(associated) == []
    assert_provider_keep(semantic_keeps(associated)[0],
                         '#include "associated_owner.hpp"  // IWYU pragma: associated', [
                             "associated_consumer.cc", "associated_owner.hpp"])
    assert [(op["subject_action"], op["disposition"], op["reason_code"])
            for op in associated["operations"]
            if op["spelling"] == '#include "template_bodies.hpp"'] == [
        ("add", "suppress", "provided_by_associated_header")]
    owner = next(record for record in associated_records
                 if record["file"]["path"] == "associated_owner.hpp")
    assert semantic_keeps(owner) == []
    assert all(use["location"]["path"] == owner["file"]["path"]
               for operation in owner["operations"]
               for symbol in operation["symbols"]
               for use in symbol["uses"])

    # Both published schemas carry the same generic provider semantics.
    schema_two, _ = analyze("template_available.cc", schema=2)
    schema_two_keep = semantic_keeps(schema_two)[0]
    assert schema_two["schema_version"] == 2
    assert all(symbol["declaration_kind"] == {
        "status": "unavailable", "value": None}
        for symbol in schema_two_keep["symbols"])


def assert_link_fails(object_file, executable, symbols, cwd=CORPUS):
    link = subprocess.run([str(CXX), str(object_file), "-o", str(executable)],
                          cwd=cwd, text=True, capture_output=True)
    assert link.returncode != 0, link.stdout + link.stderr
    output = link.stdout + link.stderr
    for symbol in symbols:
        assert symbol in output, output


def assert_driver_contracts():
    entries = [
        ("available.cc", []),
        ("duplicate_calls.cc", []),
        ("duplicate_calls.cc", []),
        ("export_consumer.cc", []),
        ("mapped_consumer.cc",
         ["-Xiwyu", "--mapping_file=providers.imp"]),
        ("associated_consumer.cc", []),
        ("default_provider.cc", []),
        ("defaulted_provider.cc", []),
        ("late_template_provider.cc", []),
        ("default_provider_instantiated.cc", []),
        ("explicit_instantiations.cc", []),
        ("explicit_instantiations.cc", []),
        ("explicit_body_first.cc", []),
        ("explicit_export.cc", []),
        ("explicit_mapped.cc", [
            "-Xiwyu", "--mapping_file=explicit_providers.imp"]),
        ("explicit_conditional.cc", ["-DFEATURE=1"]),
        ("explicit_declaration_only.cc", []),
        ("ambiguity_free.cc", ["-DFEATURE=1", "-Xiwyu",
                               "--mapping_file=ambiguity_free.imp"]),
        ("ambiguity_free.cc", ["-DFEATURE=0", "-Xiwyu",
                               "--mapping_file=ambiguity_free.imp"]),
        ("ambiguity_member.cc", ["-DFEATURE=1", "-Xiwyu",
                                 "--mapping_file=ambiguity_member.imp"]),
        ("ambiguity_member.cc", ["-DFEATURE=0", "-Xiwyu",
                                 "--mapping_file=ambiguity_member.imp"]),
        ("ambiguity_class.cc", ["-DFEATURE=1", "-Xiwyu",
                                "--mapping_file=ambiguity_class.imp"]),
        ("ambiguity_class.cc", ["-DFEATURE=0", "-Xiwyu",
                                "--mapping_file=ambiguity_class.imp"]),
        ("explicit_same_file.cc", []),
    ]
    records = analyze_with_driver(entries)

    source_records = {}
    for record in records:
        for operation in record["operations"]:
            for symbol in operation["symbols"]:
                assert all(use["location"]["path"] == record["file"]["path"]
                           for use in symbol["uses"]), (record, operation)
        index = record["invocation"]["index"]
        if record["file"]["path"] == record["invocation"]["translation_unit"]:
            source_records[index] = record
    assert set(source_records) == set(range(len(entries)))

    expected = {
        0: ("available.cc", '#include "api.inline.hpp"'),
        3: ("export_consumer.cc", '#include "export_carrier.hpp"'),
        4: ("mapped_consumer.cc", '#include "mapped_carrier.hpp"'),
        5: ("associated_consumer.cc",
            '#include "associated_owner.hpp"  // IWYU pragma: associated'),
        6: ("default_provider.cc",
            '#include "default_provider.inline.hpp"'),
        7: ("defaulted_provider.cc",
            '#include "defaulted_provider.inline.hpp"'),
        8: ("late_template_provider.cc",
            '#include "late_template_provider.inline.hpp"'),
        9: ("default_provider_instantiated.cc",
            '#include "default_provider.inline.hpp"'),
        10: ("explicit_instantiations.cc",
             '#include "explicit_api.inline.hpp"'),
        11: ("explicit_instantiations.cc",
             '#include "explicit_api.inline.hpp"'),
        12: ("explicit_body_first.cc",
             '#include "explicit_body_first.inline.hpp"'),
        13: ("explicit_export.cc",
             '#include "explicit_export_carrier.hpp"'),
        14: ("explicit_mapped.cc",
             '#include "explicit_mapped_carrier.hpp"'),
        15: ("explicit_conditional.cc",
             '#include "explicit_api.inline.hpp"'),
    }
    for index, (filename, spelling) in expected.items():
        keeps = [operation for operation in semantic_keeps(source_records[index])
                 if operation["spelling"] == spelling]
        assert len(keeps) == 1, (filename, semantic_keeps(source_records[index]))
        keep = keeps[0]
        selected_path = keep["provider"]["selected"]["value"]["path"]
        assert_provider_keep(
            keep, spelling,
            [source_records[index]["file"]["path"], selected_path])

    # Two source calls become two sorted use facts on one symbol object, and
    # duplicate compilation commands remain distinct driver invocations.
    duplicate_ids = []
    for index in (1, 2):
        record = source_records[index]
        duplicate_ids.append(record["invocation"]["id"])
        keep = semantic_keeps(record)
        assert len(keep) == 1
        symbols = keep[0]["symbols"]
        assert len(symbols) == 1, symbols
        assert symbols[0]["qualified_name"]["value"] == "TemplateFunction"
        assert len(symbols[0]["uses"]) == 2, symbols[0]
        assert symbols[0]["uses"] == sorted(
            symbols[0]["uses"],
            key=lambda use: (use["location"]["path"],
                             use["location"]["line"],
                             use["location"]["column"]))
    assert len(set(duplicate_ids)) == 2

    explicit_ids = []
    for index in (10, 11):
        record = source_records[index]
        explicit_ids.append(record["invocation"]["id"])
        keeps = semantic_keeps(record)
        assert len(keeps) == 1
        assert {symbol["qualified_name"]["value"]
                for symbol in keeps[0]["symbols"]} == {
            "Explicit", "ExplicitClassBox<int>::Member",
            "ExplicitMemberBox<int>::Member"}
    assert len(set(explicit_ids)) == 2
    assert semantic_keeps(source_records[16]) == []
    assert [(operation["subject_action"], operation["disposition"])
            for operation in source_records[16]["operations"]
            if operation["spelling"] ==
               '#include "explicit_body_first.inline.hpp"'] == [
        ("remove", "apply")]

    ambiguity_driver_cases = {
        17: ("ambiguity_free", {"two", "one"}),
        18: ("ambiguity_free", {"one"}),
        19: ("ambiguity_member", {"two", "one"}),
        20: ("ambiguity_member", {"one"}),
        21: ("ambiguity_class", {"two", "one"}),
        22: ("ambiguity_class", {"one"}),
    }
    for index, (stem, routes) in ambiguity_driver_cases.items():
        record = source_records[index]
        assert definition_diagnostics(record) == []
        expected_spelling = {
            f'#include "{stem}_{route}_carrier.hpp"' for route in routes}
        if stem == "ambiguity_class" and "one" in routes:
            expected_spelling.remove(
                '#include "ambiguity_class_one_carrier.hpp"')
            expected_spelling.add(
                '#include "ambiguity_class_one_carrier.hpp"  '
                '// IWYU pragma: associated')
        assert {operation["spelling"]
                for operation in semantic_keeps(record)} == expected_spelling
        for operation in semantic_keeps(record):
            route = ("two" if "_two_carrier" in operation["spelling"]
                     else "one")
            expected_line = 3 if route == "two" else 6
            assert {use["location"]["line"]
                    for symbol in operation["symbols"]
                    for use in symbol["uses"]} == {expected_line}
        assert [(operation["subject_action"], operation["disposition"])
                for operation in record["operations"]
                if operation["spelling"] ==
                   f'#include "{stem}_duplicate_carrier.hpp"'] == [
            ("remove", "apply")]

    assert semantic_keeps(source_records[23]) == []
    assert definition_diagnostics(source_records[23]) == []

    owner = next(record for record in records
                 if record["invocation"]["index"] == 5 and
                 record["file"]["path"].endswith("associated_owner.hpp"))
    assert semantic_keeps(owner) == []


def assert_gcc_backstop(corpus=CORPUS):
    with tempfile.TemporaryDirectory(prefix="sem004-gcc-", dir=ROOT / "build") as tmp:
        out = Path(tmp)
        for source in ("available.cc", "contexts.cc", "pure_virtual_pointer.cc",
                       "template_available.cc", "duplicate_calls.cc",
                       "template_contexts.cc",
                       "direct_after.cc", "export_consumer.cc",
                       "mapped_consumer.cc", "associated_consumer.cc",
                       "default_provider.cc", "default_provider_controls.cc",
                       "default_provider_instantiated.cc",
                       "defaulted_provider.cc", "defaulted_trivial.cc",
                       "late_same.cc", "late_template_provider.cc",
                       "uninstantiated_nondependent.cc",
                       "explicit_instantiations.cc",
                       "explicit_body_first.cc", "explicit_export.cc",
                       "explicit_mapped.cc", "explicit_declaration_only.cc",
                       "explicit_same_file.cc"):
            executable = out / source.removesuffix(".cc")
            run([CXX, "-std=c++17", "-fno-inline", "-I", ".", source,
                 "-o", executable], cwd=corpus)
            run([executable], cwd=corpus)

        control = out / "control"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "control.cc",
             "noninline.cc", "-o", control], cwd=corpus)
        run([control], cwd=corpus)

        missing_object = out / "missing.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "missing.cc", "-o", missing_object], cwd=corpus)
        assert_link_fails(missing_object, out / "missing", [
            "InlineLifecycle::InlineLifecycle()", "InlineAddress(int)",
            "InlineFree(int)", "int InlineTemplate<int>(int)"], cwd=corpus)

        template_missing_object = out / "template_missing.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "template_missing.cc", "-o", template_missing_object], cwd=corpus)
        assert_link_fails(template_missing_object, out / "template_missing", [
            "int TemplateFunction<int>(int)",
            "TemplateBox<int>::TemplateBox()",
            "TemplateBox<int>::Member() const"], cwd=corpus)

        late_extern_object = out / "late_extern.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "late_extern.cc", "-o", late_extern_object], cwd=corpus)
        assert_link_fails(late_extern_object, out / "late_extern", [
            "int TemplateFunction<int>(int)"], cwd=corpus)

        instantiations = out / "extern_instantiations.o"
        consumer = out / "extern_consumer.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "extern_instantiations.cc", "-o", instantiations], cwd=corpus)
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "extern_consumer.cc", "-o", consumer], cwd=corpus)
        extern_program = out / "extern_consumer"
        run([CXX, consumer, instantiations, "-o", extern_program], cwd=corpus)
        run([extern_program], cwd=corpus)

        for feature in (0, 1):
            for source in ("conditional_dominating.cc", "repeated_provider.cc",
                           "explicit_conditional.cc",
                           "later_definition_matching.cc",
                           "ambiguity_free.cc", "ambiguity_member.cc",
                           "ambiguity_class.cc"):
                executable = out / f"{source.removesuffix('.cc')}-{feature}"
                run([CXX, "-std=c++17", "-fno-inline", "-I", ".",
                     f"-DFEATURE={feature}", source, "-o", executable], cwd=corpus)
                run([executable], cwd=corpus)
        later_mismatched = out / "later_definition_mismatched-1"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-DFEATURE=1",
             "later_definition_mismatched.cc", "-o", later_mismatched],
            cwd=corpus)
        run([later_mismatched], cwd=corpus)
        later_sibling = out / "later_definition_sibling-0"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-DFEATURE=0",
             "later_definition_sibling.cc", "-o", later_sibling], cwd=corpus)
        run([later_sibling], cwd=corpus)
        nondominating = out / "conditional_nondominating-1"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-DFEATURE=1",
             "conditional_nondominating.cc", "-o", nondominating], cwd=corpus)
        run([nondominating], cwd=corpus)
        nondominating_object = out / "conditional_nondominating-0.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-DFEATURE=0",
             "-c", "conditional_nondominating.cc", "-o",
             nondominating_object], cwd=corpus)
        assert_link_fails(nondominating_object,
                          out / "conditional_nondominating-0",
                          ["InlineFree(int)"], cwd=corpus)

        pure_qualified_object = out / "pure_virtual_qualified.o"
        run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
             "pure_virtual_qualified.cc", "-o", pure_qualified_object],
            cwd=corpus)
        assert_link_fails(pure_qualified_object, out / "pure_virtual_qualified",
                          ["PureBase::MissingBody()"], cwd=corpus)

        for source, symbols in {
            "default_provider_missing.cc": ["MissingDefault()"],
            "pure_virtual_destructor.cc": [
                "PureDestructorBase::~PureDestructorBase()"],
            "deep_parent.cc": ["MissingDeepParent()"],
            "instantiated_nondependent.cc": ["MissingInstantiated()"],
        }.items():
            object_file = out / f"{source}.o"
            run([CXX, "-std=c++17", "-fno-inline", "-I", ".", "-c",
                 source, "-o", object_file], cwd=corpus)
            assert_link_fails(object_file,
                              out / source.removesuffix(".cc"),
                              symbols, cwd=corpus)


def assert_fixer_idempotence():
    with tempfile.TemporaryDirectory(prefix="sem004-fix-", dir=ROOT / "build") as tmp:
        corpus = Path(tmp) / "inline_definition_availability"
        shutil.copytree(CORPUS, corpus)
        cases = {
            "available.cc": [], "contexts.cc": [], "control.cc": [],
            "pure_virtual_pointer.cc": [], "template_available.cc": [],
            "duplicate_calls.cc": [], "template_contexts.cc": [],
            "export_consumer.cc": [], "mapped_consumer.cc": [
                "-Xiwyu", "--mapping_file=providers.imp"],
            "conditional_dominating.cc": ["-DFEATURE=1"],
            "repeated_provider.cc": ["-DFEATURE=0"],
            "extern_consumer.cc": [], "extern_instantiations.cc": [],
            "default_provider.cc": [], "default_provider_controls.cc": [],
            "defaulted_provider.cc": [], "defaulted_trivial.cc": [],
            "late_same.cc": [], "late_template_provider.cc": [],
            "uninstantiated_nondependent.cc": [],
            "explicit_instantiations.cc": [],
            "explicit_body_first.cc": [],
            "explicit_export.cc": [],
            "explicit_mapped.cc": [
                "-Xiwyu", "--mapping_file=explicit_providers.imp"],
            "explicit_conditional.cc": ["-DFEATURE=1"],
            "explicit_declaration_only.cc": [],
            "explicit_same_file.cc": [],
            "later_definition_matching.cc": ["-DFEATURE=1"],
            "ambiguity_free.cc": [
                "-DFEATURE=1", "-Xiwyu",
                "--mapping_file=ambiguity_free.imp"],
            "ambiguity_member.cc": [
                "-DFEATURE=1", "-Xiwyu",
                "--mapping_file=ambiguity_member.imp"],
            "ambiguity_class.cc": [
                "-DFEATURE=1", "-Xiwyu",
                "--mapping_file=ambiguity_class.imp"],
        }
        for filename, options in cases.items():
            command = [IWYU, "-I", ".", "-std=c++17", *options, filename]
            first = run(command, cwd=corpus)
            run([sys.executable, ROOT / "fix_includes.py", "--nosafe_headers",
                 "--nocomments", "--noblank_lines"], cwd=corpus,
                stdin=first.stderr)
            second = run(command, cwd=corpus)
            assert "should add these lines:" not in second.stderr, second.stderr
            assert "should remove these lines:" not in second.stderr, second.stderr
        for stem in ("ambiguity_free", "ambiguity_member", "ambiguity_class"):
            source = (corpus / f"{stem}.cc").read_text()
            assert f'#include "{stem}_two_carrier.hpp"' in source
            assert f'#include "{stem}_one_carrier.hpp"' in source
            assert f'#include "{stem}_duplicate_carrier.hpp"' not in source
        assert '#include "api.inline.hpp"' in (corpus / "available.cc").read_text()
        for filename, include in {
            "explicit_instantiations.cc":
                '#include "explicit_api.inline.hpp"',
            "explicit_body_first.cc":
                '#include "explicit_body_first.inline.hpp"',
            "explicit_export.cc":
                '#include "explicit_export_carrier.hpp"',
            "explicit_mapped.cc":
                '#include "explicit_mapped_carrier.hpp"',
            "explicit_conditional.cc":
                '#include "explicit_api.inline.hpp"',
        }.items():
            assert include in (corpus / filename).read_text(), filename
        assert "explicit_body_first.inline.hpp" not in (
            corpus / "explicit_declaration_only.cc").read_text()
        assert "unused.inline.hpp" not in (corpus / "control.cc").read_text()
        assert_gcc_backstop(corpus)


if __name__ == "__main__":
    assert_analyzer_contracts()
    assert_driver_contracts()
    assert_gcc_backstop()
    assert_fixer_idempotence()
    print("generic SEM-004 definition-availability contracts passed")
