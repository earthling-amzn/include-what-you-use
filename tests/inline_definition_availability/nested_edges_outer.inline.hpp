#pragma once

#include "nested_edges.hpp"

inline NestedEdgeBox::NestedEdgeBox() : value(NestedEdgeLeaf()) {}

inline int NestedUsesDefault(int value) {
  return value;
}

inline int NestedDefaultOuter() {
  return NestedUsesDefault();
}

inline int NestedLifecycleOuter() {
  NestedEdgeItem item;
  return 0;
}

inline int NestedLambdaPointerOuter() {
  int (*function)() = +[] { return NestedEdgeLeaf(); };
  return function();
}

inline NestedExplicitDmi::NestedExplicitDmi() {}

inline int NestedDmiOuter() {
  NestedDefaultedDmi first;
  NestedExplicitDmi second;
  return first.value + second.value;
}

inline int NestedGenericLambdaOuter() {
  int (*function)(int) = [](auto value) {
    return NestedEdgeLeaf() + value;
  };
  return function(0);
}

inline int NestedStaticAggregateOuter() {
  static NestedStaticAggregate value{};
  (void)value;
  return 0;
}
