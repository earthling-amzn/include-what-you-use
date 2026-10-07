// IWYU_ARGS: -I .

#include "tests/cxx/subdir/relative_duplicate_provider.inline.hpp"

int ReadRelativeDuplicate(const RelativeDuplicateProvider& value) {
  return value.Get();
}

/**** IWYU_SUMMARY

(tests/cxx/relative_duplicate_inline_export.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
