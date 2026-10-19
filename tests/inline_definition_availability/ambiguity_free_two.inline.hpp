#pragma once

#include "ambiguity_free_api.hpp"

template <class T>
int AmbiguousClash(T lhs, T rhs) {
  return lhs + rhs;
}
