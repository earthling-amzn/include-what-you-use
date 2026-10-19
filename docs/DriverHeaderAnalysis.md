# Complete header analysis from compilation databases

`iwyu_tool.py` can analyze a rooted header set even when some headers are not
reached by any selected translation unit. This mode is opt-in and requires
native JSON output schema 2.

```sh
iwyu_tool.py -o json --output-schema 2 \
  --configuration server -p build/server \
  --analyze-header-root /absolute/project/include \
  --analyze-header-glob '**/*.hpp' \
  --max-synthetic-header-invocations 500
```

For named databases, each database name is its configuration. Do not pass
`--configuration`:

```sh
iwyu_tool.py -o json --output-schema 2 \
  --database server=build/server --database zero=build/zero \
  --analyze-header-root /absolute/project/include \
  --analyze-header-glob '*.h' --analyze-header-glob '**/*.hpp'
```

The root must be an absolute, lexically normalized, existing physical directory.
It and selected headers may not contain symlink components. Globs use `/`, are
relative to the root, may not contain `.` or `..` components, and must each
match at least one recognized header. Selection keeps duplicate basenames in
different directories and rejects physical aliases.

A canonical manifest can replace all `--analyze-header-glob` options:

```json
{"schema_version":1,"root":"/absolute/project/include","headers":[{"path":"api/foo.hpp","sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}]}
```

The manifest must be UTF-8 canonical compact JSON with exactly one final LF,
keys in the shown order, entries sorted by `path`, no duplicate keys or paths,
and lowercase SHA-256 values matching current file bytes. The manifest itself
must be an absolute, normalized, non-symlink regular file. The driver snapshots
every selected header's bytes plus lstat/stat device, inode, mode, and file type.
It verifies that snapshot at selection, at each analysis phase boundary, and
immediately before publishing stdout. Same-byte replacement, chmod, type change,
and symlink substitution fail the transaction.

## Execution contract

Phase 1 runs every selected real compilation-database entry. It adds an exact
`--check_also` selector for every selected header, validates and buffers the
complete schema-2 stream, and retains every real context. Real contexts are
never deduplicated. A selected-header record provides coverage only with status
`correct` or `changes`; a real selected-header `skipped` or `error` record fails
the transaction instead of suppressing synthesis.

For each header without terminal coverage by physical `source_identity` in a
configuration, phase 2 runs the header directly under every distinct semantic
compile profile from that configuration's selected real entries. Profiles retain
compiler macros, include paths and their order, target options, forced inputs,
and semantic module inputs. The profile key binds both the canonical working
directory and the exact rewritten argv, so relative paths from different working
directories cannot be merged. Response files are expanded and exactly one
physical source input is replaced. Separate and joined `-x` selectors are
removed; the last effective selector, or the source suffix after `-xnone`, yields
exactly one C, C++, Objective-C, or Objective-C++ selector.

Both real and synthetic header-analysis commands remove dependency, object,
save-temps, serialized-diagnostic, coverage/profile, time-trace, optimization,
statistics, and crash-reproducer outputs. They use a syntax-only action with
crash diagnostics disabled. Module/cache output modes and other uncertain output
actions fail closed, while semantic module inputs remain unchanged. The header
itself is the synthetic main file; no wrapper or temporary source is created.
Only profiles with the same working directory and rewritten argv are
deduplicated.

`--max-synthetic-header-invocations` is a positive safety cap and defaults to
1000. The driver reserves deterministic per-database index blocks before phase
1, so covered synthetic slots remain gaps and named output stays grouped by
configuration. Command digests and invocation IDs include the actual bound
compile command, forwarded options, and exact synthetic header.

Each synthetic child must return exactly one schema-2 record for its target with
the selected configuration, physical identity, snapshotted digest, and status
`correct` or `changes`. Other valid records from a synthetic child are checked
but not emitted. Child stdout, stderr, and source bytes are buffered. A malformed
record, child failure, source drift, bad status, missing target, duplicate target,
or cap violation emits no stdout. Buffered stderr is emitted in deterministic
database, real-entry, header, and profile order.

Without any header-analysis option, `iwyu_tool.py` retains its existing command,
help defaults, indexing, and output behavior.
