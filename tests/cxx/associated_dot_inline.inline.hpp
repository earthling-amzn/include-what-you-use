//===--- associated_dot_inline.inline.hpp - test input file for iwyu -----===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#ifndef INCLUDE_WHAT_YOU_USE_TESTS_CXX_ASSOCIATED_DOT_INLINE_INLINE_HPP_
#define INCLUDE_WHAT_YOU_USE_TESTS_CXX_ASSOCIATED_DOT_INLINE_INLINE_HPP_

#include "tests/cxx/associated_dot_inline.hpp"

inline int AssociatedDotInline::GetValue() const {
  return 1;
}

#endif  // INCLUDE_WHAT_YOU_USE_TESTS_CXX_ASSOCIATED_DOT_INLINE_INLINE_HPP_

/**** IWYU_SUMMARY

(tests/cxx/associated_dot_inline.inline.hpp has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
