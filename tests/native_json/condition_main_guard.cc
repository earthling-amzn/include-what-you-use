#ifndef MAIN_FILE_FEATURE_SWITCH
#define MAIN_FILE_FEATURE_SWITCH

#include "tests/cxx/direct.h"  // IWYU pragma: keep

int ReadMainFileGuardCondition(IndirectClass* value) {
  return value->a;
}
#endif
