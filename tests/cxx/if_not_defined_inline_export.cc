// IWYU_ARGS: -I .

#include "tests/cxx/if_not_defined_export.inline.hpp"

int ReadIfNotDefined(const IfNotDefinedExport& value) {
  return value.Get();
}

/**** IWYU_SUMMARY

(tests/cxx/if_not_defined_inline_export.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
