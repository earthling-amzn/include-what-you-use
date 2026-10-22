# IWYU structured output schema, version 2

`output-v2.schema.json` extends the version-1 file-analysis record with both
typed Clang declaration-kind facts and an exact conditional-region census for
multi-configuration safety. These field families coexist in every version-2
record and are validated as one closed object. Version 1 remains the default and
is byte-compatible with the prior contract. Select version 2 with
`-Xiwyu --output_schema=2`, or with `iwyu_tool.py -o json --output-schema 2`.

## Conditional regions

Every version-2 record has `file_digest` and `conditional_regions` in addition
to the declaration fields below. `file_digest.value` equals `source_digest` and
binds region byte offsets to the exact analyzed SourceManager buffer. The driver
hashes one byte snapshot, independently raw-lexes it after C/C++ phase-2
backslash-newline splicing, and requires each submitted FileID census to match
that result exactly. The lexer excludes directive-looking bytes in comments,
ordinary literals, and raw strings. It compares every opening, alternative,
closure, directive/body offset, branch index and kind, parent identity, and
physical source location. Directive offsets name the exact physical
`directive_kind` token even when `#\\\nif`, `#\\\nelse`, or `#\\\nendif`
spelling puts the hash on the preceding physical line. Body beginnings account
for continued directive lines, and every final body end names the independently
observed `#endif`. The census contains every physical
`#if`/`#ifdef`/`#ifndef` branch and each alternative, including inactive
branches. Each child records stable source-derived region and parent identities,
opening/directive/body offsets, branch index, directive kind, expression,
per-FileID activity, source locations, and `lexical_scope`. Branch groups must
have one opening branch, ordered contiguous alternatives, at most one final
`else`, exact adjacent boundaries, acyclic parents, and complete containment of
every known child body within its known parent body.

Known operation, use, and contribution conditions carry `source_region_id` and
the complete outer-to-inner lexical `source_region_path`; expression text alone
is not an identity. Every known anchor must resolve through that exact parent
path. A Clang-proved controlling include guard remains in this lexical path when
a nested real condition supplies the anchor, but the guard is omitted from the
semantic `expression` and project-policy `active_condition`. An operation
controlled only by that guard remains semantically unconditional and therefore
has a `not_applicable` condition. For every known operation, use, and
non-`none` contribution anchor, every region in the path must exist, and the
complete path activity must agree with `active_branch`. Operation and non-`none`
contribution anchors must describe an active branch. Their `lexical_scope`
remains honest provenance and may be `non_file` or `unknown`; schema validity
does not turn that provenance into an insertion site. A conditional include
removal is the narrower exception because its edit anchor is the present include
occurrence itself: every region in that path must have `lexical_scope: file`.
Consumers that insert a missing item must independently require an
include-capable file-scope preamble before applying it.
`lexical_scope` is `file` only when both the final macro-expanded token stream
and completed AST ranges prove that the directive can accept a file-scope
include. Function, class, namespace-block, macro-expanded,
declaration-embedded, malformed, and uncertain locations are `non_file` or
`unknown`.

## Declaration facts

Every version-2 record has `source_identity`, `source_digest`,
`declaration_facts`, and `declaration_conflicts`. `source_identity` is the

Policy schema-v2 `allow_add` consumes this census as an authorization fact. An
exact condition-bound operation is policy `file` only when every region on its
exact FileID/path is uniquely present, active, and `lexical_scope: file`; any
definite non-file ancestor makes it `non_file`, and missing, repeated,
disagreeing, or unknown facts make it `unknown`. A matched allow emits
`project_policy_allow_add`, `add/apply`, and known `allow` provenance without a
warning or policy error count. This policy fact does not modify
`conditional_regions`, `edit_condition`, or consumer-side insertion checks.
normalized absolute physical path from Clang's `FileEntry` real path. It is
separate from `file.path`, which retains the source spelling used for locations
and edits. `source_digest` is SHA-256 over the exact bytes in Clang's analyzed
source buffer. The driver resolves `file.path` relative to the child invocation,
requires that resolution to equal `source_identity`, verifies the digest against
that physical file, and keys cross-configuration grouping and digest agreement
by `source_identity`. Symlink aliases of one file therefore combine, while
identically spelled relative paths in different compilation directories remain
distinct. Identity/path/digest mismatches fail the complete driver run before
any stdout is emitted.

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

A direct analyzer invocation emits an empty `declaration_conflicts` array and
must not emit incompatible kinds for one qualified entity in the same record.
`class` and `struct` are the only compatible multi-kind family. The
named-database driver validates all child facts before producing conflicts. For
each analyzed file and stable qualified name, the driver compares all observed
kinds. `class` and `struct` are compatible class-keys under Clang's C++
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
sets record status from the remaining applicable edits. A restored provider
include is inserted in the include segment before every forward declaration, as
required by native full-list and `fix_includes.py` ordering. Class-template
primaries and specializations use one canonical primary-template name, kind,
and declaration location.

## Compatibility and protocol

Schema version 1 cannot carry declaration kinds: its top-level and symbol
objects reject additional properties, and no existing typed field has this
meaning. Adding kinds to v1 would violate its strict versioning contract.

The analyzer and driver therefore keep version 1 as the default. A driver that
requests version 2 validates the complete child structure and cross-field
semantics of both declaration and conditional-region families before emitting
any stdout. It rejects version-1 children, missing or extra fields at any depth,
malformed region identities, paths, offsets, states, anchors, lexical scopes,
operations and desired states, unknown declaration kinds, duplicate or unsorted
facts, non-empty native conflict claims, source-digest mismatches, and same-path
records produced from different bytes. Version-1 output is passed through
unchanged and receives no new aggregation behavior.

All stream framing, invocation identity, operation, desired-state, and ordering
rules not replaced above are inherited unchanged from
`IWYUOutputSchema.md`.
