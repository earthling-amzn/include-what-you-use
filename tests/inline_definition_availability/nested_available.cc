#include "nested_leaf.inline.hpp"
#include "nested_macro.hpp"
#include "nested_outer.inline.hpp"

int main() {
  return CALL_NESTED_OUTER() == 42 ? 0 : 1;
}
