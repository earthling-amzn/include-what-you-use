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
specialization, or an instantiated class-template member. Explicit
instantiation definitions own one obligation at Clang's point of instantiation.
A prior selected `extern template` declaration transfers body ownership to
another translation unit. Explicit non-inline specializations retain ordinary
out-of-line ownership.

The obligation follows Clang's canonical specialization and recursively peels
function-template and instantiated-member patterns to the source-written body.
Concrete template traversal reports at the caller/instantiation site and
reconsiders pattern-identical nodes as well as dependent calls resolved by the
instantiation. Uninstantiated primaries and unresolved dependent calls create no
obligation. Clang's semantic parent map and `EvaluatedExprVisitor` exclude
`decltype`, `sizeof`-like and `noexcept` operands, unused default arguments, and
discarded `if constexpr` branches. Deleted/defaulted functions, virtual dispatch
to a pure function, and pure member-pointer formation are excluded. An
explicitly qualified pure call still requires the pure declaration's body.

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

Every covering edge is pinned by its includer `FileID`, directive location,
includee `FileID`, spelling, and path. Output exposes a
`semantic_definition_provider` keep operation with AST symbol uses, the selected
spelling/path, concrete physical chain, use condition, and occurrence range.
This pin is re-applied after ordinary duplicate pruning, so an equal spelling
cannot substitute for the entered occurrence. No include is synthesized from an
absent or unproved body.

An absent ordinary-inline body emits `missing_inline_definition`; an absent
template pattern emits `missing_template_definition`; and a visible definition
without a proved entered/dominating provider emits
`definition_provider_unproven`. Schema versions 1 and 2 use their existing
operation, provider, symbol-use, condition, range, and diagnostic structures.
The opaque corpus validates deterministic output, two fixer passes, both schemas,
conditional configurations, direct/associated/exported/mapped/repeated provider
instances, GCC `-fno-inline` compile-link-run success, and expected unresolved
symbols for missing bodies.
