//===--- macro_body_dependencies-ambiguous-b.h - test ---------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#ifndef INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_AMBIGUOUS_B_H_
#define INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_AMBIGUOUS_B_H_

struct MacroBodyAmbiguousB {
  int ambiguous_member() const;
};

#endif  // INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_AMBIGUOUS_B_H_
