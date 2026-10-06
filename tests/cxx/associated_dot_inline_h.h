//===--- associated_dot_inline_h.h - test input file for iwyu ------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#ifndef INCLUDE_WHAT_YOU_USE_TESTS_CXX_ASSOCIATED_DOT_INLINE_H_H_
#define INCLUDE_WHAT_YOU_USE_TESTS_CXX_ASSOCIATED_DOT_INLINE_H_H_

class AssociatedDotInlineH {
 public:
  int GetValue() const;
};

#endif  // INCLUDE_WHAT_YOU_USE_TESTS_CXX_ASSOCIATED_DOT_INLINE_H_H_

/**** IWYU_SUMMARY

(tests/cxx/associated_dot_inline_h.h has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
