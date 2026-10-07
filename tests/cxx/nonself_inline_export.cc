//===--- nonself_inline_export.cc - test input file for iwyu ------------===//
// IWYU_ARGS: -I .

#include "tests/cxx/nonself_inline_export_provider.inline.hpp"

// IWYU: NonselfExportType needs a declaration
int ReadNonselfExport(const NonselfExportType& value) {
  // IWYU: NonselfExportType is defined in
  return value.Get();
}

/**** IWYU_SUMMARY

tests/cxx/nonself_inline_export.cc should add these lines:
#include "tests/cxx/nonself_export_type.hpp"

tests/cxx/nonself_inline_export.cc should remove these lines:

The full include-list for tests/cxx/nonself_inline_export.cc:
#include "tests/cxx/nonself_export_type.hpp"  // for NonselfExportType
#include "tests/cxx/nonself_inline_export_provider.inline.hpp"  // for NonselfExportType::Get

***** IWYU_SUMMARY */
