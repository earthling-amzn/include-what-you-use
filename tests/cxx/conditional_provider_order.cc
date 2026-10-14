// IWYU_ARGS: -I . -DENABLE_LATE_CONDITIONAL_PROVIDER=1

#if ENABLE_LATE_CONDITIONAL_PROVIDER
class LateConditionalProvider;
LateConditionalProvider* BeforeConditionalInclude();

#include "tests/cxx/conditional_provider_late.h"

int ReadLateConditionalProvider(LateConditionalProvider* value) {
  return value->value;
}
#endif

/**** IWYU_SUMMARY

(tests/cxx/conditional_provider_order.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
