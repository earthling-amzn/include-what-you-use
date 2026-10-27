#pragma once

#include "nested_api.hpp"

inline int NestedOuter() {
  return NestedLeaf();
}
