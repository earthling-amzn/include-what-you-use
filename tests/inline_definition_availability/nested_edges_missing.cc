#include "nested_edges_outer.inline.hpp"

int main() {
  NestedEdgeBox box;
  return box.value + NestedDefaultOuter() + NestedLifecycleOuter() +
                 NestedLambdaPointerOuter() + NestedDmiOuter() +
                 NestedGenericLambdaOuter() + NestedStaticAggregateOuter() == 42 ? 0 : 1;
}
