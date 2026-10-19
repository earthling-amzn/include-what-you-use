# Semantic provider safety boundary

## SEM-003 provider evidence

Schema-v1 include additions retain a selected provider and candidate set only
when every motivating declaration has a concrete observed include instance for
the provider and the selected spelling resolves to that path from the analyzed
includer. Quoted mappings are never bound through a translation-unit-wide union
of same-spelling observations. Unseen mappings and mappings that resolve to a
different file in the analyzed includer remain unavailable.

A known chain is sliced from one declaration `FileID` ancestry containing the
motivating use's exact `FileID`. Every motivating use must produce the same
chain. The implementation never searches the physical `FileEntry` graph, where
edges from repeated conditional instances could otherwise be combined into a
chain that preprocessing never observed. The chain remains evidence only and
never authorizes an edit.

Associated-header satisfaction remains the existing one-hop rule: a source file
may inherit only a direct include of its associated header. Structured output
makes that decision explicit with `provided_by_associated_header` and a chain.
A provider two edges below the associated header remains a
`missing_direct_include`. No header-cycle behavior was added.

Mapped candidates are resolved with Clang's include lookup from the analyzed
includer. The resolved path must occur on the motivating declaration's concrete
ancestry. Relative spellings can therefore identify the right local provider,
while unresolved, shadowed, or merely globally observed endpoints remain
unavailable rather than guessing from the filesystem.

## SEM-004 inline and template definition availability

SEM-004 creates a concrete body obligation for each potentially evaluated ODR
use of an ordinary semantically-inline function, an implicit function-template
specialization, or an instantiated class-template member. Each source-written
explicit-instantiation definition owns one obligation at its recovered request
point. A prior selected `extern template` declaration transfers body ownership to
another translation unit. Explicit non-inline specializations retain ordinary
out-of-line ownership.

The obligation follows Clang's canonical specialization and recursively peels
function-template and instantiated-member patterns to the source-written body.
Concrete template traversal reports at the caller/instantiation site and
reconsiders pattern-identical nodes as well as dependent calls resolved by the
instantiation. Non-dependent calls in an uninstantiated templated entity remain
inert; the dedicated concrete-instantiation traversal emits them once at the
instantiation site. Uninstantiated primaries and unresolved dependent calls
create no obligation. Clang's evaluated-expression visitor excludes `decltype`,
`sizeof`-like and `noexcept` operands, unused default arguments, and discarded
`if constexpr` branches. Its result is cached per complete expression. The
parent walk uses IWYU's explicit AST stack iteratively with cycle detection;
missing or cyclic ancestry is unknown and therefore fails closed by preserving
or diagnosing the body rather than suppressing the obligation.

An omitted `CXXDefaultArgExpr` is traversed in its concrete call context, so an
evaluated default expression owns body obligations at that call site. Explicit
arguments, unevaluated calls, and defaults in never-instantiated templates stay
excluded. A concretely used first-declaration defaulted constructor, destructor,
copy/move constructor, or copy/move assignment does not require a body for the
defaulted function itself. Instead, its generated initializer/body graph and
direct base/member destruction graph create obligations for selected lifecycle
functions. This covers arrays, bases, in-class member initializers, and nested
defaulted members with a per-use cycle guard. Trivial subobjects add nothing.
An out-of-class defaulted inline definition remains an ordinary required
provider. Pure virtual destructors require a definition when concrete object
lifecycle destroys the base; forming a member pointer or making a virtual call
to other pure methods remains excluded, while an explicitly qualified pure call
still requires its body.

An explicit-instantiation definition is recovered from its source-written
non-`extern` `template` request even when Clang keeps an earlier `extern
template` location as the specialization's point of instantiation. The request
is accepted only when the final specialization is an explicit definition, its
instantiation pattern has a selected source-written body (including `= default`),
and the recovered request is active and names that function or enclosing class.
Explicit-instantiation declarations create no local body obligation. Multiple
same-name requests receive distinct source points, while members produced by one
explicit class instantiation share its class request. This source point anchors
the exact entered body-provider proof and is retained in both structured output
and the legacy fixer projection.

A definition is available only through one concrete entered `FileID` ancestry.
IncludePicker supplies candidate public, mapping-file, pragma-export, and
verified dotted-inline intent. The preprocessor proof separately binds the
candidate to an exact root directive, resolved physical path, entered includee
`FileID`, and source condition that dominates the use. Every nested edge must
belong to that same ancestry and be semantically unconditional. An associated
owner contributes one exact root hop before the same proof. Skipped re-entry,
wrong repeated instances, inactive or sibling branches, shadowed paths, unknown
locations, and chains assembled from a physical `FileEntry` union fail closed.
Ordinary inline bodies may be introduced later in the same definition domain;
an earlier declaration provider is retained separately when needed.

Every covering root is pinned by its includer `FileID`, directive location,
includee `FileID`, logical include token, and resolved path. Output exposes a
`semantic_definition_provider` keep operation only for the consumer's exact
direct root occurrence. Its provider chain begins at that operation's analyzed
file and ends at the selected direct include path. The internal proof may
continue through export or mapping intermediates to the ultimate definition,
but those deeper paths are not published as the selected operation's chain.
The operation spelling and range retain exact physical source text, including a
pragma comment; the selected provider spelling is always the logical quoted or
angled token. Intermediate records never inherit consumer use locations. They
retain their own includes only through source-local semantic obligations or
existing export/keep analysis. This pin is re-applied after ordinary duplicate
pruning, so an equal spelling cannot substitute for the entered occurrence. No
include is synthesized from an absent or unproved body. An implicit template
instantiation may use a later definition at its legal point of instantiation.
A later same-TU definition is accepted only from the actual entered definition
declaration after its active condition path is proved to dominate the legal
instantiation/use point. Matching active branches and unconditional definitions
cover the use; inactive, sibling, and mismatched branches remain unproven. A
later header provider is retained only through its exact entered root occurrence
and physical chain. Explicit instantiation declarations/definitions and `extern
template` keep their separate ownership rules.

Repeated observations are stored by include operation and canonical function.
Structured output emits one symbol object per qualified identity with sorted,
deduplicated use facts. Exact provider-proof queries are memoized by their full
input key. On the review fixture `/tmp/sem004-adversarial/perf` (1,000 unrelated
headers and 5,000 calls), baseline `411eec0` used 1.89 s, 421,540 KiB RSS, and
3,007,009 output bytes; its schema-v2 driver rejected 5,000 duplicate symbol
objects. After grouping, three analyzer runs used 1.93/1.76/1.57 s and
257,880/257,980/258,064 KiB RSS, emitted 1,732,264 bytes as one symbol with 5,000
uses, and the real driver accepted the record in 2.75 s at 258,024 KiB RSS.
After replacing capped recursive parent-map queries with cached evaluated
subtrees and an iterative cycle-safe AST-stack walk, three absolute-path reruns
used 1.65/1.55/1.63 s and 258,452/258,700/258,440 KiB RSS with the same
1,732,264-byte output, one symbol, and 5,000 uses. The non-inline control remained
620,944 bytes (baseline 1.01 s/120,056 KiB; after
1.08/1.09/1.11 s and 119,580/119,632/119,728 KiB).

An absent ordinary-inline body emits `missing_inline_definition`; an absent
template pattern emits `missing_template_definition`; and a visible definition
without a proved entered/dominating provider emits
`definition_provider_unproven`. Schema versions 1 and 2 use their existing
operation, provider, symbol-use, condition, range, and diagnostic structures.
The opaque corpus validates deterministic output, two fixer passes, both schemas,
conditional configurations, direct/associated/exported/mapped/repeated and
later-entered provider instances, omitted defaults, generated defaulted
lifecycle, pure destructors, deep/cyclic-safe ancestry, instantiated and
uninstantiated template bodies, GCC `-fno-inline` compile-link-run success, and
expected unresolved symbols for missing bodies.
