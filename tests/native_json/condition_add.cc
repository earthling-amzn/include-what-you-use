#define ENABLE_NATIVE_JSON_CONDITION 1

#if ENABLE_NATIVE_JSON_CONDITION
#include "tests/cxx/direct.h"  // IWYU pragma: keep

int ReadNativeJsonCondition(IndirectClass* value) {
  return value->a;
}
#endif
