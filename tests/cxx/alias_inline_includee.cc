// IWYU_ARGS: -I .

#include "tests/cxx/alias_legacy.inline.hpp"
#include "tests/cxx/alias_inl.inline.hpp"
#include "tests/cxx/alias_test_suffix.inline.hpp"

// IWYU:
AliasLegacy GetAliasLegacy() { return {}; }
// IWYU:
AliasInl GetAliasInl() { return {}; }
// IWYU:
AliasTestSuffix GetAliasTestSuffix() { return {}; }

/**** IWYU_SUMMARY

tests/cxx/alias_inline_includee.cc should add these lines:
#include "tests/cxx/alias_inl.inl"
#include "tests/cxx/alias_legacy-inl.h"
#include "tests/cxx/alias_test_suffix_test.hpp"

tests/cxx/alias_inline_includee.cc should remove these lines:
- #include "tests/cxx/alias_inl.inline.hpp"  // lines XX-XX
- #include "tests/cxx/alias_legacy.inline.hpp"  // lines XX-XX
- #include "tests/cxx/alias_test_suffix.inline.hpp"  // lines XX-XX

The full include-list for tests/cxx/alias_inline_includee.cc:
#include "tests/cxx/alias_inl.inl"  // for AliasInl
#include "tests/cxx/alias_legacy-inl.h"  // for AliasLegacy
#include "tests/cxx/alias_test_suffix_test.hpp"  // for AliasTestSuffix

***** IWYU_SUMMARY */
