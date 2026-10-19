#include "ambiguity_free_api.hpp"
#if FEATURE
#include "ambiguity_free_two_carrier.hpp"
template int AmbiguousClash<int>(int, int);
#endif
#include "ambiguity_free_one_carrier.hpp"
#include "ambiguity_free_duplicate_carrier.hpp"
template int AmbiguousClash<int>(int);

int main() {
#if FEATURE
  return AmbiguousClash(1) + AmbiguousClash(1, 2) == 4 ? 0 : 1;
#else
  return AmbiguousClash(1) == 1 ? 0 : 1;
#endif
}
