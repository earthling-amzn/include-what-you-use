// IWYU_ARGS: -I .

#include "tests/cxx/direct_first.inline.hpp"
#include "tests/cxx/direct_second.inline.hpp"

int ReadBoth(const DirectFirst& first, const DirectSecond& second) {
  return first.Get() + second.Get();
}

/**** IWYU_SUMMARY

(tests/cxx/direct_unrelated_inline_export.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
