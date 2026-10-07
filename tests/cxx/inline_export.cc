//===--- inline_export.cc - test input file for iwyu ---------------------===//
// IWYU_ARGS: -I .

#include "tests/cxx/inline_export_provider.inline.hpp"

int ReadInlineExport(const InlineExportProvider& value) {
  return value.Get();
}

/**** IWYU_SUMMARY

(tests/cxx/inline_export.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
