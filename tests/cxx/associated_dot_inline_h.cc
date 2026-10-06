//===--- associated_dot_inline_h.cc - test input file for iwyu -----------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

// IWYU_ARGS: -I .

#include "tests/cxx/associated_dot_inline_h.inline.h"

int ReadHValue(const AssociatedDotInlineH& value) {
  return value.GetValue();
}

/**** IWYU_SUMMARY

(tests/cxx/associated_dot_inline_h.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
