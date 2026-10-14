#define CONDITION_TOKEN_A 1
#define CONDITION_TOKEN_B 1

#if CONDITION_TOKEN_A /* removed comment */ \
    && CONDITION_TOKEN_B
#include "tests/cxx/direct.h"  // IWYU pragma: keep

int ReadConditionTokens(IndirectClass* value) {
  return value->a;
}
#endif
