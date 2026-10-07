// IWYU_ARGS: -I .

#define ENABLE_EXPORT_EDGE 1
#define ELSE_EXPORT_INLINE_HPP
#include "tests/cxx/else_export.inline.hpp"
#include "tests/cxx/literal_export.inline.hpp"
#include "tests/cxx/elif_export.inline.hpp"
#include "tests/cxx/digraph_export.inline.hpp"

// IWYU: ElseExport needs a declaration
int ReadElse(const ElseExport& value) {
  // IWYU: ElseExport is defined in
  return value.Get();
}
// IWYU: LiteralExport needs a declaration
int ReadLiteral(const LiteralExport& value) {
  // IWYU: LiteralExport is defined in
  return value.Get();
}
// IWYU: ElifExport needs a declaration
int ReadElif(const ElifExport& value) {
  // IWYU: ElifExport is defined in
  return value.Get();
}
// IWYU: DigraphExport needs a declaration
int ReadDigraph(const DigraphExport& value) {
  // IWYU: DigraphExport is defined in
  return value.Get();
}

/**** IWYU_SUMMARY

tests/cxx/conditional_edge_inline_export.cc should add these lines:
#include "tests/cxx/digraph_export.hpp"
#include "tests/cxx/elif_export.hpp"
#include "tests/cxx/else_export.hpp"
#include "tests/cxx/literal_export.hpp"

tests/cxx/conditional_edge_inline_export.cc should remove these lines:

The full include-list for tests/cxx/conditional_edge_inline_export.cc:
#include "tests/cxx/digraph_export.hpp"  // for DigraphExport
#include "tests/cxx/digraph_export.inline.hpp"  // for DigraphExport::Get
#include "tests/cxx/elif_export.hpp"  // for ElifExport
#include "tests/cxx/elif_export.inline.hpp"  // for ElifExport::Get
#include "tests/cxx/else_export.hpp"  // for ElseExport
#include "tests/cxx/else_export.inline.hpp"  // for ElseExport::Get
#include "tests/cxx/literal_export.hpp"  // for LiteralExport
#include "tests/cxx/literal_export.inline.hpp"  // for LiteralExport::Get

***** IWYU_SUMMARY */
