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
order; the first matching rule in an action family supplies its decision and
provenance.

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
a safety rule. POLICY-002 enables `deny_add` with includer, includee, file kind,
symbol, and the optional invocation configuration supplied by
`-Xiwyu --configuration=<name>`. Active-condition facts remain unavailable.
