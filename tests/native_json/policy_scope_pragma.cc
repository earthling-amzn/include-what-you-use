#define POLICY_PRAGMA_CONDITION 1

// IWYU pragma: no_include "tests/cxx/indirect.h"
#include "tests/cxx/direct.h"  // IWYU pragma: keep

#if POLICY_PRAGMA_CONDITION
int ReadPolicyPragmaPrecedence(IndirectClass* value) {
  return value->a;
}
#endif
