void NonFileKeepDeclaration(
#if defined(NON_FILE_KEEP)
#include "tests/conditional_regions/empty.hpp"  // IWYU pragma: keep
#endif
    int value);
