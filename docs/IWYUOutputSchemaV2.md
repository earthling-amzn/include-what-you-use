# IWYU structured output schema, version 2

`output-v2.schema.json` extends the version-1 file-analysis record with typed
Clang declaration-kind facts needed for multi-configuration safety. Version 1
remains the default and is unchanged. Select version 2 with
`-Xiwyu --output_schema=2`, or with `iwyu_tool.py -o json --output-schema 2`.

## Declaration facts

Every version-2 record has `source_digest`, `declaration_facts`, and
`declaration_conflicts`. `source_digest` is SHA-256 over the exact bytes in
Clang's analyzed source buffer. The driver verifies it against the source before
emission and requires every named configuration for one canonical path to
report the same digest.

`declaration_facts` is a sorted, unique list of declarations observed in
non-ignored semantic uses of the analyzed file. Function-local declarations are
excluded because a qualified spelling alone is not a stable identity across
lexical scopes. A fact contains:

- `qualified_name`: the stable qualified entity name reported by Clang;
- `kind`: `class`, `struct`, `union`, `scoped_enum`, `unscoped_enum`,
  `class_template`, `typedef`, or `using_alias`;
- `declaration`: the exact Clang declaration location.

Version-2 operation symbols also contain typed `declaration_kind`
availability. Macro-only facts remain `unavailable` when the retained macro
provenance does not identify a Clang declaration node. Producers and consumers
must not infer a kind from a rendered declaration or diagnostic text.

A direct analyzer invocation emits an empty `declaration_conflicts` array.
The named-database driver validates all child facts before producing conflicts.
For each analyzed file and stable qualified name, the driver compares all
observed kinds. `class` and `struct` are compatible class-keys under Clang's C++
redeclaration semantics. Every other multi-kind set is incompatible, including
union/record, scoped/unscoped enum, enum/record, template/non-template, and all
alias conflicts. Each reported conflict carries the record's `source_digest`;
entries and their `(configuration, kind)` declarations are sorted and exact.

When a configuration proposes a forward declaration for an incompatible entity,
the driver changes only that addition and the matching provider removal to
`report`. Schema-v2 producers attach the same Clang entity identity to both
operations, so unrelated removals with similar include spellings are untouched. It
keeps unrelated unanimous operations applicable, preserves a known complete
desired state with the provider retained and the unsafe declaration absent, and
sets record status from the remaining applicable edits. Class-template
primaries and specializations use one canonical primary-template name, kind,
and declaration location.

## Compatibility and protocol

Schema version 1 cannot carry declaration kinds: its top-level and symbol
objects reject additional properties, and no existing typed field has this
meaning. Adding kinds to v1 would violate its strict versioning contract.

The analyzer and driver therefore keep version 1 as the default. A driver that
requests version 2 validates the complete child structure and cross-field
semantics before emitting any stdout. It rejects version-1 children, missing or
extra fields at any depth, malformed operations and desired states, unknown
kinds, duplicate or unsorted facts, non-empty native conflict claims, source
digest mismatches, and same-path records produced from different bytes.
Version-1 output is passed through unchanged and receives no new aggregation
behavior.

All stream framing, invocation identity, operation, desired-state, and ordering
rules not replaced above are inherited unchanged from
`IWYUOutputSchema.md`.
