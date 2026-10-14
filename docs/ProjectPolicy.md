# Project policy format

IWYU accepts one project policy with `-Xiwyu --project_policy=<path>`. The file
uses the YAML 1.2/JSON data model and this closed version-1 grammar:

```yaml
schema_version: 1
rules:
  - id: stable-rule-id
    action: deny_add
    match:
      includer: { glob: "src/*.hpp" }
      includee: { regex: ".*[.]inline[.]hpp" }
      file_kind: header
      active_condition: "defined(FEATURE)"
      symbol: "project::Type"
      configuration: fastdebug
```

The top-level keys are `schema_version` and `rules`. Both are required. `rules`
may be empty. Every rule requires a unique `id`, an `action`, and a non-empty
`match`. Rule IDs match `[A-Za-z][A-Za-z0-9_.-]*`.

Actions are a closed enum:

- `deny_add`
- `deny_remove`
- `keep`
- `associate`
- `export`
- `prefer_provider`
- `report_only`

The `includer` and `includee` matchers each contain exactly one `glob` or
`regex`. Matching is case-sensitive against the whole slash-normalized path or
include spelling without `""`/`<>` delimiters. Regexes use LLVM's POSIX ERE
dialect. Globs use LLVM `GlobPattern` semantics: `*` and `?` can match `/`,
`**` has no special recursive meaning, bracket classes are supported, and a
backslash escapes the next byte.

`file_kind` is one of `source`, `header`, `inline_header`, or `other`.
`active_condition`, `symbol`, and `configuration` are non-empty exact strings.
A rule matches only when every supplied dimension matches. Rules retain file
order. The addition family is `{deny_add}` and the removal family is
`{deny_remove, keep}`. Lookup scans once in file order while ignoring actions
outside the requested family; the first matching family rule supplies its
actual action and provenance.

Parsing is transactional and fail-closed. IWYU rejects unknown or duplicate
keys, duplicate rule IDs, malformed node shapes, aliases, anchors, explicit
tags, block scalars, nulls where a value is required, embedded NUL bytes,
multiple YAML documents, invalid regexes, and invalid globs before analysis.
Diagnostics name the canonical policy path and rule/field context. Policy-load
failures write only to stderr and exit nonzero.

The parser recognizes every version-1 action and match dimension. Runtime
effects are enabled separately as migration phases establish the facts needed
to apply them safely. A loaded rule that requests a recognized but unavailable
action or fact fails with a capability diagnostic; IWYU never silently ignores
a safety rule. POLICY-002 enables `deny_add`; POLICY-007 enables `deny_remove`
and `keep`. These actions match includer, includee, file kind, symbol, and the
optional invocation configuration supplied by `-Xiwyu
--configuration=<name>`. Active-condition provenance is emitted in structured
output, but `active_condition` policy matching remains unavailable until unknown
facts can propagate as an explicit indeterminate first-match decision.

## `deny_add` runtime semantics

IWYU evaluates `deny_add` after pragma and prefix-header suppressions and before
rendering the final desired state. It considers only missing desired include
lines that do not already carry a stronger decision. A match produces an
`add/suppress` operation with reason code `project_policy_deny_add`, known policy
file/rule/`deny` provenance, unchanged symbol and provider evidence, and a
warning diagnostic naming the policy entry. The include remains absent from the
accepted desired state. Text output emits a parse-safe warning and no fixer add.
A suppression is a policy violation for `--error`, but it is not an applied edit
and does not by itself change a structured record's status from `correct`.

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

Policy includee matching always uses the delimiter-free logical direct include
token. Removal operation and desired-state spelling preserve the physical source
line, including whitespace, a macro include operand, or a trailing comment.
`file_kind: header` excludes dotted inline headers, which have kind
`inline_header`.

Only `deny_add`, `deny_remove`, and `keep` are currently enabled. Other declared
actions and all `active_condition` matches fail capability validation rather
than being ignored. Existing source pragmas and project-side application
guardrails remain in force.
