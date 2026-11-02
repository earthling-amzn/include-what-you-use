# Project policy format

IWYU accepts one project policy with `-Xiwyu --project_policy=<path>`. The file
uses the YAML 1.2/JSON data model and a closed versioned grammar. Schema version
1 remains unchanged. Schema version 2 adds `allow_add` and the
`edit_lexical_scope` match fact. Schema version 3 adds `allow_remove` and the
`replacement` match fact:

```yaml
schema_version: 3
rules:
  - id: stable-rule-id
    action: allow_remove
    match:
      includer: { glob: "src/*.hpp" }
      includee: { regex: ".*[.]inline[.]hpp" }
      file_kind: header
      replacement: forward_declaration
```

The schema-2 form remains:

```yaml
schema_version: 2
rules:
  - id: stable-rule-id
    action: allow_add
    match:
      includer: { glob: "src/*.hpp" }
      includee: { regex: ".*[.]inline[.]hpp" }
      file_kind: header
      active_condition: "defined(FEATURE)"
      edit_lexical_scope: file
      symbol: "project::Type"
      configuration: fastdebug
```

The top-level keys are `schema_version` and `rules`. Both are required. `rules`
may be empty. Every rule requires a unique `id`, an `action`, and a non-empty
`match`. Rule IDs match `[A-Za-z][A-Za-z0-9_.-]*`.

Actions are a closed enum. Schema version 1 accepts:

- `deny_add`
- `deny_remove`
- `keep`
- `associate`
- `export`
- `prefer_provider`
- `report_only`

Schema version 2 accepts all version-1 actions plus `allow_add`. Schema version 3
accepts all version-2 actions plus `allow_remove`. Versions 1 and 2 reject
`allow_remove` and `replacement`; version 1 continues to reject `allow_add` and
`edit_lexical_scope` as unknown grammar entries.

The `includer` and `includee` matchers each contain exactly one `glob` or
`regex`. Matching is case-sensitive against the whole slash-normalized path or
include spelling without `""`/`<>` delimiters. Regexes use LLVM's POSIX ERE
dialect. Globs use LLVM `GlobPattern` semantics: `*` and `?` can match `/`,
`**` has no special recursive meaning, bracket classes are supported, and a
backslash escapes the next byte.

`file_kind` is one of `source`, `header`, `inline_header`, or `other`.
`symbol` and `configuration` are non-empty exact strings.
`active_condition` is also a non-empty exact string, but its runtime fact is
typed as one of `known`, `not_applicable`, or `unknown`. A
condition-constrained rule matches only a `known` fact with byte-for-byte equal
source-derived expression text. It does not match `not_applicable`, which means
the relevant source is unconditional after header-guard elision. IWYU does not
parse, normalize, or prove logical equivalence between policy and source
expressions.

Schema version 2's `edit_lexical_scope` is exactly `file` or `non_file`. Its
runtime fact is likewise `known`, `not_applicable`, or `unknown`. IWYU derives it
from the active condition's exact FileID and complete `source_region_path` in
the finalized conditional-region census. It is `file` only when every exact
ancestor and leaf is uniquely present, active, and file-scoped. It is
`non_file` when that complete valid path contains a definite non-file scope.
Missing, repeated, disagreeing, inactive, or unknown census facts produce
`unknown`. An unconditional condition produces `not_applicable`. This is an
authorization fact only; it neither changes the schema-v2 census nor bypasses
application safety checks.

Schema version 3's `replacement` value is exactly `forward_declaration`. Its
runtime fact is typed as `known`, `not_applicable`, or `unknown`. A known fact
means native semantic analysis has linked the exact physical include occurrence
to one or more typed desired forward declarations. A definite absence does not
match and allows a later broad removal rule to run. Ambiguous occurrence,
declaration, condition, or desired-state evidence is unknown and stops ordered
lookup without claiming policy provenance.

A rule matches only when every supplied dimension matches. Rules retain file
order. The addition family is `{allow_add, deny_add}` and the removal family is
`{allow_remove, deny_remove, keep}`. Lookup scans once in file order while ignoring actions
outside the requested family. A known condition or scope mismatch continues the
scan. The first definite match supplies its actual action and provenance. If
every other dimension matches a constrained rule but a required condition,
scope, or replacement fact is `unknown`, lookup returns `indeterminate` at that rule and does
not consider a later rule. Diagnostics identify the indeterminate fact. A rule
without the corresponding constraint does not need that fact unless an earlier
constrained rule already made lookup indeterminate. Thus deny-before-allow wins,
and allow-before-deny permits.

Parsing is transactional and fail-closed. IWYU rejects unknown or duplicate
keys, duplicate rule IDs, malformed node shapes, aliases, anchors, explicit
tags, block scalars, nulls where a value is required, embedded NUL bytes,
multiple YAML documents, invalid regexes, and invalid globs before analysis.
Diagnostics name the canonical policy path and rule/field context. Policy-load
failures write only to stderr and exit nonzero.

The parser recognizes every action and match dimension in the selected policy
schema. Runtime effects are enabled separately as migration phases establish the
facts needed to apply them safely. A loaded rule that requests a recognized but
unavailable action or fact fails with a capability diagnostic; IWYU never
silently ignores a safety rule. POLICY-002 enables `deny_add`; POLICY-007 enables
`deny_remove` and `keep`. Schema-v2 `allow_add` is enabled only for native JSON
output schema 2. Every `allow_add` rule must contain an exact non-empty
`active_condition` and exactly `edit_lexical_scope: file`; broad, unconditional,
or non-file allows are rejected before analysis. Schema-v3 `allow_remove` is also
enabled only for native JSON output schema 2, and every rule must contain exactly
`replacement: forward_declaration`; unrestricted removal allows are rejected.
Text output and JSON schema 1 reject a policy containing either allow action
before analysis. A configuration match
still requires the exact invocation configuration supplied by
`-Xiwyu --configuration=<name>`.

For a proposed include, IWYU derives the active-condition fact from all full
symbol uses that motivate that exact provider. The fact is known only when the
uses have the same condition status, expression, active branch, and source
branch identity. No motivating use, unavailable source location, repeated
include instances, or disagreement produces `unknown`. For removal, IWYU
recovers the condition from the exact direct include occurrence by includer,
logical include spelling, and physical line. Missing or ambiguous occurrence
identity produces `unknown` instead of an invented unconditional fact.

## Addition runtime semantics

IWYU evaluates the ordered `{allow_add, deny_add}` family after pragma and
prefix-header suppressions and before rendering the final desired state. It
considers only missing desired include lines that do not already carry a
stronger decision.

A `deny_add` match produces an `add/suppress` operation with reason code
`project_policy_deny_add`, known policy file/rule/`deny` provenance, unchanged
symbol and provider evidence, and a warning diagnostic naming the policy entry.
The include remains absent from the accepted desired state. Text output emits a
parse-safe warning and no fixer add. A suppression is a policy violation for
`--error`, but it is not an applied edit and does not by itself change a
structured record's status from `correct`.

An exact `allow_add` match preserves the analyzer's `add/apply` recommendation
and complete desired state, changes the reason code to
`project_policy_allow_add`, and emits known policy file/rule/`allow` provenance.
It emits no warning and contributes no `--error` violation count. The applied
edit remains visible as a schema-v2 `changes` operation. Existing pragma,
`no_include`, prefix-header, and prior structured decisions remain stronger and
are never replaced by an allow.

## Removal runtime semantics

IWYU evaluates the removal family after pragma, `no_include`, and prefix-header
decisions. It considers only direct present include lines that remain undesired
and have no stronger structured decision. Forward declarations are never policy
removal candidates.

A matching `deny_remove` rule emits `remove/suppress` with reason code
`project_policy_deny_remove` and known policy file/rule/`deny` provenance. It
restores the exact physical source line to desired state, emits one warning, and
counts as a policy violation for `--error`. A matching `keep` rule emits
`remove/keep` with reason code `project_policy_keep` and `keep` provenance,
restores the exact physical source line, emits no warning, and does not affect
`--error`. Non-add provider fields remain `not_applicable`.

## Indeterminate policy-fact semantics

When lookup is indeterminate, IWYU preserves source safety without claiming the
constrained rule matched. A proposed addition becomes `add/suppress`; a proposed
removal becomes `remove/suppress` and the exact source line remains in desired
state. An unknown condition uses reason code
`project_policy_active_condition_unknown`; an unknown lexical scope uses
`project_policy_edit_lexical_scope_unknown`. Both emit a warning diagnostic
naming the indeterminate fact, policy file, and rule where lookup stopped, and
count as violations for `--error`. Their structured `policy` field remains
`unavailable`; normal matched-rule provenance and policy decisions are emitted
only for a definite match. Text output emits the same parse-safe warning and no
corresponding fixer edit.

Policy includee matching always uses the delimiter-free logical direct include
token. Removal operation and desired-state spelling preserve the physical source
line, including whitespace, a macro include operand, or a trailing comment.
`file_kind: header` excludes dotted inline headers, which have kind
`inline_header`.

Only bounded schema-v2 `allow_add`, schema-v3 replacement-backed
`allow_remove`, `deny_add`, `deny_remove`, and `keep` are currently enabled. Other declared actions fail capability validation rather
than being ignored. Existing source pragmas and project-side application
guardrails remain in force.
