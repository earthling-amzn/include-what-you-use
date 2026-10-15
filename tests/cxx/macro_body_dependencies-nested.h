//===--- macro_body_dependencies-nested.h - test --------------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#ifndef INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_NESTED_H_
#define INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_NESTED_H_

#include "tests/cxx/macro_body_dependencies-nested-provider.h"

#define MACRO_BODY_INNER(value)                                              \
  ::macro_body_dependencies::nested_transform(value)

#endif  // INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_NESTED_H_

/**** IWYU_SUMMARY

(tests/cxx/macro_body_dependencies-nested.h has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
