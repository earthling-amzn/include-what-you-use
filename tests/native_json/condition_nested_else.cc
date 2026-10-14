#define ENABLE_OUTER_CONDITION 1
#define SELECT_LEFT_CONDITION 0

#if ENABLE_OUTER_CONDITION
#if SELECT_LEFT_CONDITION
#else
#include "tests/cxx/direct.h"  // IWYU pragma: keep

int ReadNestedElseCondition(IndirectClass* value) {
  return value->a;
}
#endif
#endif
