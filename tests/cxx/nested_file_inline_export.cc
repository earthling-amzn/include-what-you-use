// IWYU_ARGS: -I .

#include "tests/cxx/nested_file_export.inline.hpp"

int ReadNestedFile(const NestedFileExport& value) {
  return value.Get();
}

/**** IWYU_SUMMARY

(tests/cxx/nested_file_inline_export.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
