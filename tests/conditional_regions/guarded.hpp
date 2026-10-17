#ifndef TESTS_CONDITIONAL_REGIONS_GUARDED_HPP_
#define TESTS_CONDITIONAL_REGIONS_GUARDED_HPP_

#include "tests/cxx/direct.h"

inline int GuardOnlyValue(IndirectClass* value) {
  return value->a;
}

#if defined(GUARDED_FEATURE)
#include "tests/conditional_regions/all.hpp"

inline int GuardedFeatureValue(Mc002Jit* value) {
  return value->value;
}
#endif

#endif  // TESTS_CONDITIONAL_REGIONS_GUARDED_HPP_
