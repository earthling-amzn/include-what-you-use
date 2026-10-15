//===--- macro_body_dependencies-provider.h - test ------------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#ifndef INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_PROVIDER_H_
#define INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_PROVIDER_H_

namespace macro_body_dependencies {

struct Payload {
  explicit Payload(int input) : value(input) {}

  int value;
};

}  // namespace macro_body_dependencies

#endif  // INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_PROVIDER_H_
