//===--- associated_dot_inline.cc - test input file for iwyu -------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

// IWYU_ARGS: -I .

// A dotted .inline.hpp header belongs to the same compilation unit as the
// corresponding .cc and .hpp files.

#include "tests/cxx/associated_dot_inline.inline.hpp"

int ReadValue(const AssociatedDotInline& value) {
  return value.GetValue();
}

/**** IWYU_SUMMARY

(tests/cxx/associated_dot_inline.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
