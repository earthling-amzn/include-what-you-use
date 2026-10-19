#include "api.hpp"

#if FEATURE
#include "api.inline.hpp"
int FeatureUse() { return InlineFree(4); }
#endif

int main() {
#if FEATURE
  return FeatureUse() == 5 ? 0 : 1;
#else
  return 0;
#endif
}
