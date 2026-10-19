#include "ambiguity_member_api.hpp"
#if FEATURE
#include "ambiguity_member_two_carrier.hpp"
template int AmbiguousMemberBox<int>::Second(int, int);
#endif
#include "ambiguity_member_one_carrier.hpp"
#include "ambiguity_member_duplicate_carrier.hpp"
template int AmbiguousMemberBox<int>::First(int);

int main() {
  AmbiguousMemberBox<int> box;
#if FEATURE
  return box.First(1) + box.Second(1, 2) == 4 ? 0 : 1;
#else
  return box.First(1) == 1 ? 0 : 1;
#endif
}
