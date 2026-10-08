#ifndef ASSOC003_INLINE_CONSUMER_INLINE_HPP
#define ASSOC003_INLINE_CONSUMER_INLINE_HPP
#include "tests/cxx/assoc003_inline_consumer.hpp"
#include "tests/cxx/assoc003_pair_provider.hpp"
#include "tests/cxx/assoc003_pair_provider.inline.hpp"
inline int Assoc003InlineConsumer::Read(
    const Assoc003PairProvider& value) const {
  return value.Get() + ReadAssoc003Pair(value);
}
#endif

/**** IWYU_SUMMARY

tests/cxx/assoc003_inline_consumer.inline.hpp should add these lines:

tests/cxx/assoc003_inline_consumer.inline.hpp should remove these lines:
- #include "tests/cxx/assoc003_pair_provider.hpp"  // lines XX-XX

The full include-list for tests/cxx/assoc003_inline_consumer.inline.hpp:
#include "tests/cxx/assoc003_inline_consumer.hpp"  // for Assoc003InlineConsumer
#include "tests/cxx/assoc003_pair_provider.inline.hpp"  // for Assoc003PairProvider, Assoc003PairProvider::Get, ReadAssoc003Pair

***** IWYU_SUMMARY */
