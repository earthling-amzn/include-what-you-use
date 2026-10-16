# Semantic provider safety boundary

## SEM-003 provider evidence

Schema-v1 include additions now retain a selected provider and candidate set when
all motivating uses resolve the selected spelling to one physical path. A
shortest observed include chain is emitted when that selected path is already
reachable. The chain is evidence only and never authorizes an edit.

Associated-header satisfaction remains the existing one-hop rule: a source file
may inherit only a direct include of its associated header. Structured output
makes that decision explicit with `provided_by_associated_header` and a chain.
A provider two edges below the associated header remains a
`missing_direct_include`. No header-cycle behavior was added.

Mapped candidates reuse an observed include spelling only when every observed
instance names the same physical file. Conflicting or unobserved mappings remain
unavailable rather than guessing from the filesystem.

## SEM-004 inline-definition availability

The implemented source-fact slice covers direct calls, member calls, and the
constructor/destructor callbacks already modeled by IWYU. When Clang resolves a
called definition to a directly included, verified generic dotted
`.inline.<header-extension>` endpoint, that definition is recorded as a full
use. Verification still requires that the dotted inline header directly and
unconditionally self-include its exact plain counterpart.

An odr-used function explicitly declared `inline` with no visible definition
produces `missing_inline_definition` at the call or lifecycle site. This catches
the common declared-but-never-defined member, free-function, constructor, and
destructor shapes before the GCC link backstop.

This slice deliberately does not guess an absent provider filename, infer
availability through conditional includes, prove arbitrary re-export chains, or
remove broad project policy. The existing SEM-004 policy keep for direct
`.inline.hpp` includes remains required until those cases have equivalent source
facts. Standalone GCC compile/link probes remain part of the opaque corpus.
