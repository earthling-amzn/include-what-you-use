#include "api.hpp"

#if FEATURE
#include "api.inline.hpp"
#endif

int main() { return InlineFree(4) == 5 ? 0 : 1; }
