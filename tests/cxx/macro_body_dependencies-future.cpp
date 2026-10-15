//===--- macro_body_dependencies-future.cpp - test ------------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#include "tests/cxx/macro_body_dependencies.h"

#ifdef MACRO_BODY_DISABLED
#error "disabled conditional macro unexpectedly defined"
#endif

int main() {
  auto payload = MACRO_BODY_MAKE(5);
  if (payload.value != 5)
    return 1;
  ::macro_body_dependencies::MemberPayload member_payload(5);
  if (MACRO_BODY_MEMBER(member_payload) != 7)
    return 2;
  if (MACRO_BODY_FREE(5) != 15)
    return 3;
  if (MACRO_BODY_OUTER(5) != 6)
    return 4;
  if (MACRO_BODY_CONDITIONAL(5) != 9)
    return 5;
  return 0;
}
