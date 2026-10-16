# IWYU structured output schema, version 2

`output-v2.schema.json` extends the version-1 file-analysis record with typed
Clang declaration-kind facts needed for multi-configuration safety. Version 1
remains the default and is unchanged. Select version 2 with
`-Xiwyu --output_schema=2`, or with `iwyu_tool.py -o json --output-schema 2`.

## Declaration facts

Every version-2 record has `declaration_facts` and `declaration_conflicts`.
`declaration_facts` is a sorted, unique list of declarations observed in
semantic uses of the analyzed file. A fact contains:

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
For each analyzed file and stable qualified name, it reports a conflict when at
least two named configurations observe at least two different kinds. Conflict
entries and their `(configuration, kind)` declarations are sorted and exact.

If one side is a `typedef` or `using_alias`, another side is forward-declarable,
and a configuration proposes a forward declaration for the entity, that
configuration's complete record is made report-only. The record has `skipped`
status, unavailable desired state, and no applied operation. Holding the whole
record prevents an include removal from being applied without the now-blocked
forward declaration.

## Compatibility and protocol

Schema version 1 cannot carry declaration kinds: its top-level and symbol
objects reject additional properties, and no existing typed field has this
meaning. Adding kinds to v1 would violate its strict versioning contract.

The analyzer and driver therefore keep version 1 as the default. A driver that
requests version 2 rejects version-1 children, malformed facts, unknown kinds,
duplicate or unsorted facts, and non-empty native conflict claims before it
emits any stdout. Version-1 output is passed through unchanged and receives no
new aggregation behavior.

All stream framing, invocation identity, operation, desired-state, and ordering
rules not replaced above are inherited unchanged from
`IWYUOutputSchema.md`.
