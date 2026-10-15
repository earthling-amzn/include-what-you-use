//===--- macro_body_dependencies-disabled.h - test ------------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#ifndef INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_DISABLED_H_
#define INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_DISABLED_H_

struct MacroBodyDisabled {
  static int disabled_member();
};

#endif  // INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_DISABLED_H_
