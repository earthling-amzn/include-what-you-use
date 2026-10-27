#pragma once

#include "nested_edges.hpp"

inline int NestedControlOuter() {
  (void)sizeof(&NestedEdgeLeaf);
  if constexpr (false) {
    return NestedEdgeLeaf();
  }
  auto dormant = [] { return NestedEdgeLeaf(); };
  (void)dormant;
  return 0;
}
