//===--- macro_body_dependencies-provider.cpp - test ----------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#include "tests/cxx/macro_body_dependencies-conditional.h"
#include "tests/cxx/macro_body_dependencies-free.h"
#include "tests/cxx/macro_body_dependencies-member.h"
#include "tests/cxx/macro_body_dependencies-nested-provider.h"

namespace macro_body_dependencies {

int MemberPayload::member(int delta) const {
  return value + delta;
}

int transform(int value, int delta) {
  return value * delta;
}

int conditional_transform(int value) {
  return value + 4;
}

int nested_transform(int value) {
  return value + 1;
}

}  // namespace macro_body_dependencies
