#ifndef ASSOC003_PLAIN_NEGATIVE_HPP
#define ASSOC003_PLAIN_NEGATIVE_HPP
#include "tests/cxx/assoc003_pair_provider.hpp"
#include "tests/cxx/assoc003_pair_provider.inline.hpp"
class Assoc003PlainNegative {
 private:
  Assoc003PairProvider value_;
};
#endif

/**** IWYU_SUMMARY

tests/cxx/assoc003_plain_negative.hpp should add these lines:

tests/cxx/assoc003_plain_negative.hpp should remove these lines:
- #include "tests/cxx/assoc003_pair_provider.inline.hpp"  // lines XX-XX

The full include-list for tests/cxx/assoc003_plain_negative.hpp:
#include "tests/cxx/assoc003_pair_provider.hpp"  // for Assoc003PairProvider

***** IWYU_SUMMARY */
