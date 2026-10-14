//===--- conditional_provider_fwd_decl.cc - test input for iwyu -----------===//
//
// This source is analyzed with the provider branch enabled, but its
// unconditional pointer declaration must also compile in configurations where
// the branch is disabled. A conditional definition cannot make the
// unconditional forward declaration redundant.
//
//===----------------------------------------------------------------------===//

// IWYU_ARGS: -I . -DENABLE_CONDITIONAL_PROVIDER=1

#if ENABLE_CONDITIONAL_PROVIDER
#include "tests/cxx/indirect.h"
#endif

class IndirectClass;

IndirectClass* LookupConditionalProvider();

#if ENABLE_CONDITIONAL_PROVIDER
int ReadConditionalProvider(IndirectClass* value) {
  return value->a;
}
#endif

/**** IWYU_SUMMARY

(tests/cxx/conditional_provider_fwd_decl.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
