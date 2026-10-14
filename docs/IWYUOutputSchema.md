# IWYU structured output schema, version 1

`output-v1.schema.json` defines one record in IWYU's JSON Lines output.
OUTPUT-001 defined the schema; OUTPUT-002 implements native and compilation-
database production.

## Stream protocol

Structured output uses JSON Lines because `iwyu_tool.py` runs one IWYU child per
compilation command. Every non-empty line on the structured-output stream is one
complete UTF-8 JSON object validating against `output-v1.schema.json`. There is
no surrounding array and no multi-line record.

JSON writes from concurrent children are not assumed atomic. In structured mode,
the driver captures each child's stdout and stderr separately, validates complete
child groups, and emits them in compilation-database `invocation.index` order.
Native Clang/IWYU startup and fatal diagnostics remain on stderr. Diagnostics
belonging to an analysis are encoded in the record. A non-JSON stdout line in
structured mode is a protocol error.

Direct IWYU invocation emits records in normalized target-file order. The driver
buffers child results and emits invocation groups in index order, not child
completion order. The required non-null `invocation.id` is the final tie-breaker
for duplicate compilation commands.

For one legacy `-p` database, `invocation.index` remains the original entry
index. With repeatable `--database NAME=PATH`, indices span the complete ordered
databases: each database starts after the full entry count of all preceding
databases, and each command retains its original local entry offset. This makes
identical local indices and commands from different databases distinct while
preserving gaps left by source selection. Named JSON invocations receive the
exact driver-owned `--configuration=NAME`; the driver validates matching
`invocation.configuration`, `analyzed_configurations`, and operation
contributions before emitting any stdout. Records and stderr remain grouped in
database order, then original entry order. The driver performs no semantic
multi-configuration aggregation.

Human diagnostics remain the default. Direct structured output uses
`-Xiwyu --output_format=json`; the compilation-database driver uses `-o json`.

## Record identity

One record describes one target file observed from one translation-unit
invocation and configuration:

```text
(schema_version, invocation.index, invocation.id,
 invocation.translation_unit, file.path)
```

Configuration has explicit availability. Multi-config aggregation must preserve
per-configuration contributions before producing a merged operation.

The driver creates canonical command bytes as compact UTF-8 JSON with keys in
this exact order and no insignificant whitespace:

```json
{"directory":"<realpath with / separators>","file":"<realpath with / separators>","arguments":["..."]}
```

`arguments` is the exact post-splitting, post-response-file-expansion argument
array after compiler-wrapper removal and before IWYU-only arguments are added;
individual argument strings are not rewritten. Including `directory` makes
relative path-bearing arguments unambiguous. Strings use UTF-8 directly for
non-ASCII characters, never escape `/`, escape `"` and `\\`, use the short JSON
escapes for backspace/tab/newline/form-feed/carriage-return, and encode other
U+0000–U+001F values as lowercase `\\u00xx`. `command_digest` is `sha256:` plus
the 64 lowercase hexadecimal digits of SHA-256 over those bytes.

The driver derives `invocation.id` as
`inv-<database-index>-<digest-prefix>`. Database index is zero-based decimal,
left-padded to at least six digits; `digest-prefix` is the first 12 digest hex
digits. Direct IWYU uses index zero. Duplicate commands retain distinct database
indices and therefore distinct IDs. The validator enforces the ID/index/digest
relationship; golden vectors in the examples lock serialization.

## Top-level fields

- `schema_version`: integer `1`.
- `record_type`: `file_analysis`.
- `tool`: IWYU, Clang, and source-revision metadata.
- `invocation`: stable ID/index, translation unit, configuration availability,
  and required canonical command digest.
- `file`: analyzed path and kind.
- `status`: `correct`, `changes`, `skipped`, or `error`.
- `associated_headers`: typed relationships with machine reason codes and human
  reasons, rather than path strings alone.
- `analyzed_configurations`: explicit available complete configuration-name set
  against which operation contributions are checked.
- `operations`: ordered recommendations and policy dispositions.
- `desired_state`: explicitly known/complete or unavailable.
- `diagnostics`: structured analysis, policy, startup, and parse diagnostics.

Known-empty collections are emitted as empty arrays. Unknown data is never
represented by omission or an ambiguous empty collection.

## Operations: subject versus disposition

An operation separates the underlying recommendation from what the current
layer decided to do with it:

- `subject_action`: `add`, `remove`, or `keep`.
- `disposition`: `apply`, `keep`, `suppress`, or `report`.

Examples:

```text
add/apply       add the item
remove/apply    remove the item
keep/keep       retain a present item
add/suppress    policy rejected a proposed addition
remove/report   unsafe removal is reported for a human
```

This avoids overloading a single `suppress` action without saying what was
suppressed.

Other required operation fields:

- `item_kind`: `include` or `forward_declaration`.
- `spelling`: exact source spelling.
- `source_range`: explicit known/unknown/unavailable/not-applicable range.
- `reason_code`: stable machine identifier.
- `reason`: human explanation, not an API.
- `symbols`: declarations and per-use completeness/origin/macro/condition data
  motivating the operation.
- `provider`: separately available selected provider, candidates, and chain.
- `edit_condition`: condition under which an addition/removal should be applied;
  this is distinct from each motivating use's condition.
- `policy`: typed policy file, rule, and decision provenance.
- `multi_config`: typed per-configuration contributions.

Operation IDs (`op-1`, `op-2`, ...) are unique within a file record.

## Completeness

Each symbol contains one or more ordered use objects. Every use has an explicit
`requirement`:

- `full`
- `forward_declaration`
- `none`
- `unknown`

Each use also records `origin` (`ast`, `macro`, `heuristic`, or `unknown`) and its
own typed condition and macro provenance. Provenance is per-use because one
operation may be motivated by several symbols, one symbol may have mixed
AST/macro uses, and use sites may be controlled by different conditions. Symbol
declaration and qualified-name availability remain shared by the grouped symbol.

## Typed availability

Every field that may not be computable uses a typed availability wrapper:

```json
{"status": "known", "value": "server-fastdebug"}
{"status": "unknown", "value": null}
{"status": "unavailable", "value": null}
{"status": "not_applicable", "value": null}
```

- `unknown`: the producer supports the field but could not determine this value.
- `unavailable`: this producer/version does not compute the field.
- `not_applicable`: the field has no meaning for this operation.

Known values are schema-constrained by provenance class. Provider details cannot
contain arbitrary objects:

- `selected`: available provider reference with balanced quoted/angled include
  spelling and normalized path
- `candidates`: available unique, sorted provider-reference array
- `chain`: available ordered normalized-path array ending at the selected path

Provider paths use `/`, contain no `.` or `..` component or duplicate separator,
and have no trailing separator except root. Selected provider, candidate set, and
chain remain independently available; for example, selected may be known while
chain is unavailable.

A known selected provider must occur in known candidates. For an include
operation its include token matches selected spelling. A known chain requires a
known selected provider, starts at the analyzed file, and ends at the selected
normalized path.

Condition known value contains expression, location availability, and active
branch. Macro known value contains name and definition/expansion availability.
Policy known value contains policy file, rule ID, and decision. Multi-config
known value contains at least one typed per-configuration contribution.

The native producer computes source-derived active conditions for AST uses and
operations. Unconditional source emits `not_applicable`, a complete branch path
emits `known`, and a location or expression that cannot be recovered safely
emits `unknown`; it never treats unknown provenance as unconditional. A
Clang-proved controlling include guard is framing and is omitted from the
condition.

Condition expressions preserve source tokens with whitespace runs collapsed;
macros are not expanded. `#ifdef X` is rendered as `defined(X)` and `#ifndef X`
as `!defined(X)`. An `#elif E` branch is the conjunction of the negated prior
sibling tests and `(E)`; `#else` is the conjunction of all negated prior sibling
tests. Nested groups are an ordered parenthesized conjunction from outermost to
innermost. The known location identifies the active innermost branch directive,
and `active_branch` is true because AST uses and direct includes arise only from
the selected branch. Branch identity also includes source directive locations
and the Clang `FileID` internally, so textually identical conditions in separate
groups or repeated include instances are not interchangeable providers.

## Associated headers

Each association records:

- path
- relationship: declarations, inline definitions, explicit, or heuristic
- reason code
- human reason

OUTPUT-002 must retain the reason at the point where `AddAssociatedHeader` is
called; the current `set<IwyuFileInfo*>` loses it.

## Duplicate occurrence fallback

Schema version 1 identifies an operation by item kind and spelling, not by a
specific source occurrence. Its desired-state invariant also forbids an applied
remove operation for an item key that remains desired. When a file contains
duplicate occurrences and IWYU would remove only the surplus occurrence,
emitting `remove/apply` would therefore describe a false whole-item removal.
The native producer emits that file as `status: "skipped"`, with no operations,
an unavailable desired state, and a `duplicate_occurrence_unrepresentable`
diagnostic. A later schema may add occurrence identity; version 1 deliberately
keeps this explicit whole-file fallback.

## Multi-config provenance

`multi_config` is an availability value. When known, it contains one or more
contributions with:

- configuration name
- subject action (`add`, `remove`, `keep`, or `none`)
- disposition (`not_applicable` exactly when subject is `none`)
- typed condition provenance

An empty array is not used for unknown or unnamed configuration state. A normal
single-config producer emits a known singleton contribution when
`--configuration` is supplied and `not_applicable` otherwise. The contribution
repeats the operation's exact subject, disposition, and edit condition. In a
multi-config aggregate,
every operation has one contribution for every name in
`analyzed_configurations`; missing recommendations use `none/not_applicable`.
Configuration names are unique. Every non-`none` contribution has the same
subject action as the aggregate and exactly the same typed condition as
`edit_condition`; `none` contributions always use a not-applicable condition.
Apply/keep/suppress aggregates require that disposition from every contribution.
A report aggregate requires at least one report contribution and may include
explicit no-recommendation contributions. The multi-config driver must retain
these conditions and must not apply an unconditional union of
configuration-specific additions.

## Ranges and columns

Locations are one-based. Columns are one-based UTF-8 byte columns, matching
Clang's presumed source-location column convention. Range end is exclusive.

An added item with no selected insertion point uses `not_applicable`; version 1
requires add subjects to use that status. Remove and keep subjects refer to
present items and therefore may be known, unknown, or unavailable, but never
not-applicable. Known endpoints use the same path and end must not precede start.
A present item whose line is known but exact columns are not retained uses
`unavailable`; it must not fabricate column 1. Current
`OneIncludeOrForwardDeclareLine` keeps only line numbers, so OUTPUT-002 must
retain richer locations or explicitly emit unavailable ranges.

## Desired state

`desired_state` has explicit status, `complete`, and items:

```json
{"status": "known", "complete": true, "items": []}
{"status": "unavailable", "complete": false, "items": null}
```

For `correct` and `changes`, version 1 requires known, complete desired state.
For `skipped` and `error`, unavailable state is valid. Desired items must be
unique by `(item_kind, spelling)`.

When desired state is known:

- `add/apply` appears;
- `add/suppress` and `add/report` remain absent;
- `remove/apply` remains absent;
- `remove/keep`, `remove/suppress`, and `remove/report` remain present;
- `keep/keep` and `keep/report` remain present.

These rules make desired state the accepted post-disposition source state, not
the analyzer's pre-policy ideal.

## Status invariants

- `correct`: no applied add/remove operation; desired state known/complete.
- `changes`: at least one applied add/remove; desired state known/complete.
- `skipped`: no applied edits; at least one diagnostic.
- `error`: no applied edits; at least one error diagnostic.

The example validator enforces these semantic constraints in addition to JSON
Schema.

## Deterministic ordering

Within a record:

1. associations sort by path, relationship, reason code and are unique by that
   tuple;
2. operations sort by subject rank (`remove`, `add`, `keep`), disposition rank
   (`apply`, `keep`, `suppress`, `report`), item-kind rank (`include`,
   `forward_declaration`), full range key (availability rank, path, start
   line/column, end line/column), spelling, then reason code;
3. duplicate underlying operations are forbidden by `(subject_action,
   item_kind, spelling, source_range)`, so the key above has no unresolved tie;
4. operation IDs are assigned `op-1`, `op-2`, ... only after sorting and are not
   themselves sort keys;
5. desired items retain final source order and are unique by kind/spelling;
6. symbols sort by available qualified name (or name fallback), then name, and
   are unique by that identity;
7. uses sort by path, line, column, origin rank (`ast`, `macro`, `heuristic`,
   `unknown`), requirement rank (`full`, `forward_declaration`, `none`,
   `unknown`), then canonical condition and macro values; exact duplicate use
   identities are forbidden;
8. provider candidates sort by normalized path then spelling; configuration
   contributions sort by configuration name.

Across records, direct IWYU sorts by file path. `iwyu_tool.py` emits compilation
invocations in database index order and file records in child order. Consumers
must not need to sort completion-order output.

## Versioning

The schema is strict (`additionalProperties: false`). Any field addition,
removal, type change, closed-enum expansion, or semantic incompatibility creates
a new schema version/file. Producers select one version. Consumers reject an
unsupported schema version.

Reason-code additions do not require a schema bump because `reason_code` is a
patterned string. Existing reason-code meanings must not change.

## OUTPUT-002 producer profile

The native producer emits `correct` and `changes` records with complete desired
state, plus `skipped` records when schema v1 cannot represent a duplicate
occurrence edit without contradicting retained desired membership. It preserves
applied add/remove operations, current keep/suppress decision reasons, exact
physical spelling for present-item operations, grouped motivating symbol uses,
associated-header reasons, and recoverable IWYU/Clang diagnostics. It emits
source-derived per-use and edit conditions, known policy provenance for native
policy decisions, exact optional invocation configuration names, and known
singleton contributions for configured runs. Unsupported provider chains,
macro-origin details, and exact source ranges use typed unavailable values; an
individual condition that cannot be recovered safely uses `unknown`.

Native output sorts file records and operations deterministically. The driver
expands response files using the compiler's GNU or clang-cl tokenization,
computes the canonical digest before adding IWYU protocol options, preserves
original database indices and duplicate commands, rejects reserved option
injection, validates LF-framed child output atomically, and emits child groups
and native stderr in database-index order.

## Remaining native-model work

The producer still leaves these fields unavailable until later semantic and
policy tasks retain them without inference:

- exact macro-versus-AST origin and macro definition/expansion provenance;
- normalized selected/candidate provider paths and provider chains outside the
  currently verified dotted-inline endpoint;
- exact source ranges for edits;
- active-condition project-policy matching with explicit indeterminate
  first-match behavior;
- native multi-database aggregation, condition-aware insertion anchors, and
  declaration-kind conflict provenance.

Unavailable fields use the typed wrappers defined above. A supported fact that
cannot be recovered for one location uses `unknown`. Producers must never invent
values or use arbitrary payload objects.

## Security

Paths, symbols, reasons, policy IDs, and diagnostics are untrusted data.
Consumers must not interpolate them into shell commands. Structured output does
not contain instructions to execute and should not include source snippets,
environment variables, or full compiler commands by default. `command_digest`
is preferred to command text.

## Examples and validation

- Positive JSONL: `tests/schema/output-v1/positive.jsonl`
- Negative JSONL: `tests/schema/output-v1/negative-*.jsonl`
- Expected negative reasons: `tests/schema/output-v1/negative-expectations.json`
- Invocation ID vectors: `tests/schema/output-v1/invocation-id-vectors.json`
- Validator: `tests/validate_output_schema.py`

The validator checks JSON Schema, semantic invariants, ordering, uniqueness,
operation/desired-state consistency, and that each negative fixture fails for
its named reason. It returns CTest skip code 77 only when the optional
`jsonschema` package is unavailable.
