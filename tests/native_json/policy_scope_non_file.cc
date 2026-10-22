#define POLICY_NON_FILE_CONDITION 1

#include "tests/cxx/direct.h"  // IWYU pragma: keep

int ReadPolicyScopeNonFile() {
#if POLICY_NON_FILE_CONDITION
  IndirectClass* value = nullptr;
  return value->a;
#else
  return 0;
#endif
}
