// IWYU_ARGS: -I .

#include "tests/cxx/assoc003_own_plain.inline.hpp"

int UseAssoc003OwnPlain(const Assoc003OwnPlain& value) {
  return value.Get();
}

/**** IWYU_SUMMARY

(tests/cxx/assoc003_own_plain.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
