// IWYU_ARGS: -I .

#include "tests/cxx/assoc003_nonself_provider.hpp"
#include "tests/cxx/assoc003_nonself_provider.inline.hpp"

int UseAssoc003Nonself(const Assoc003NonselfProvider& value) {
  return value.Get() + ReadAssoc003Nonself(value);
}

/**** IWYU_SUMMARY

(tests/cxx/assoc003_nonself_exception.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
