#include "explicit_api.hpp"

#if FEATURE
#include "explicit_api.inline.hpp"
template int ConditionalExplicit<int>(int);
#endif

int main() {
#if FEATURE
  return ConditionalExplicit(1) == 5 ? 0 : 1;
#else
  return 0;
#endif
}
