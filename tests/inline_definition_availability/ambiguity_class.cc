#include "ambiguity_class_api.hpp"
#if FEATURE
#include "ambiguity_class_two_carrier.hpp"
template struct AmbiguousTwo::Box<int>;
#endif
#include "ambiguity_class_one_carrier.hpp"  // IWYU pragma: associated
#include "ambiguity_class_duplicate_carrier.hpp"
template struct AmbiguousOne::Box<int>;

int main() {
  AmbiguousOne::Box<int> one;
#if FEATURE
  AmbiguousTwo::Box<int> two;
  return one.Get() + two.Get() == 3 ? 0 : 1;
#else
  return one.Get() == 1 ? 0 : 1;
#endif
}
