#include "nested_edges_leaf.inline.hpp"
#include "nested_edges_outer.inline.hpp"

int main() {
  NestedEdgeBox box;
  return box.value + NestedDefaultOuter() + NestedLifecycleOuter() +
                 NestedLambdaPointerOuter() + NestedDmiOuter() +
                 NestedGenericLambdaOuter() == 42 ? 0 : 1;
}
