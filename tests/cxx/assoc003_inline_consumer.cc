// IWYU_ARGS: -I .
#include "tests/cxx/assoc003_inline_consumer.inline.hpp"

int ReadAssoc003InlineConsumer(
    const Assoc003InlineConsumer& consumer,
    const Assoc003PairProvider& value) {
  return consumer.Read(value);
}

/**** IWYU_SUMMARY

(tests/cxx/assoc003_inline_consumer.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
