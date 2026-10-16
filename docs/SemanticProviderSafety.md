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

## SEM-004 inline-definition availability

The implemented source-fact slice covers direct calls, member calls,
constructor/destructor callbacks, ordinary function addresses, member pointers,
and Clang-resolved explicit function-template specializations. Clang's semantic
parent map and `EvaluatedExprVisitor` decide whether the expression is
potentially evaluated. `decltype`, `sizeof`-like and `noexcept` operands, unused
default arguments, discarded `if constexpr` branches, pure virtual dispatch,
pure virtual member-pointer formation, and unused deleted/defaulted controls do
not create diagnostics. A direct call explicitly qualified with its pure base
class suppresses virtual dispatch and still requires the pure declaration's
out-of-class definition, so it remains diagnostic. Primary and implicitly
instantiated templates remain excluded.

When Clang resolves an ODR-used definition to a directly included, verified
generic dotted `.inline.<header-extension>` endpoint, that definition is
recorded as a full use. Verification still requires that the dotted inline
header directly and unconditionally self-include its exact plain counterpart.

A potentially evaluated ODR-use of an eligible function explicitly declared
`inline` with no visible definition produces `missing_inline_definition` at the
call, address, member-pointer, or lifecycle site. This catches ordinary member,
free-function, constructor, destructor, and explicit-specialization shapes
before the GCC `-fno-inline` link backstop.

This slice deliberately does not guess an absent provider filename, infer
availability through conditional includes, prove arbitrary re-export chains, or
remove broad project policy. The existing SEM-004 policy keep for direct
`.inline.hpp` includes remains required until those cases have equivalent source
facts. Standalone GCC compile/link probes remain part of the opaque corpus.
