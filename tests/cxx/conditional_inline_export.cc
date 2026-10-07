//===--- conditional_inline_export.cc - test input file for iwyu --------===//
// IWYU_ARGS: -I .

#define ENABLE_EXPORT 1
#include "tests/cxx/conditional_inline_export_provider.inline.hpp"

// IWYU: ConditionalInlineExportProvider needs a declaration
int ReadConditionalExport(const ConditionalInlineExportProvider& value) {
  // IWYU: ConditionalInlineExportProvider is defined in
  return value.Get();
}

/**** IWYU_SUMMARY

tests/cxx/conditional_inline_export.cc should add these lines:
#include "tests/cxx/conditional_inline_export_provider.hpp"

tests/cxx/conditional_inline_export.cc should remove these lines:

The full include-list for tests/cxx/conditional_inline_export.cc:
#include "tests/cxx/conditional_inline_export_provider.hpp"  // for ConditionalInlineExportProvider
#include "tests/cxx/conditional_inline_export_provider.inline.hpp"  // for ConditionalInlineExportProvider::Get

***** IWYU_SUMMARY */
