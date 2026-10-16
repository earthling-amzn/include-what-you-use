//===--- macro_body_dependencies.h - test ---------------------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#ifndef INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_H_
#define INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_H_

#include "tests/cxx/macro_body_dependencies-provider.h"
#include "tests/cxx/macro_body_dependencies-member.h"
#include "tests/cxx/macro_body_dependencies-free.h"
#include "tests/cxx/macro_body_dependencies-conditional.h"
#include "tests/cxx/macro_body_dependencies-nested.h"
#include "tests/cxx/macro_body_dependencies-unrelated.h"
#include "tests/cxx/macro_body_dependencies-disabled.h"
#include "tests/cxx/macro_body_dependencies-ambiguous-a.h"
#include "tests/cxx/macro_body_dependencies-ambiguous-b.h"
#define MACRO_BODY_MAKE(input)                                                \
  ::macro_body_dependencies::Payload(input)

#define MACRO_BODY_MEMBER(payload) ((payload).member(2))
#define MACRO_BODY_AMBIGUOUS(payload) ((payload).ambiguous_member())
#define MACRO_BODY_FREE(value)                                                \
  ::macro_body_dependencies::transform((value), 3)
#define MACRO_BODY_OUTER(value) MACRO_BODY_INNER(value)

#if defined(MACRO_BODY_USE_MEMBER)
#define MACRO_BODY_CONDITIONAL(value) ((value).member(4))
#else
#define MACRO_BODY_CONDITIONAL(value)                                        \
  ::macro_body_dependencies::conditional_transform(value)
#endif

#if 0
#define MACRO_BODY_DISABLED() MacroBodyDisabled::disabled_member()
#endif

inline int macro_body_dependencies_anchor() {
  return 0;
}

#endif  // INCLUDE_WHAT_YOU_USE_TESTS_CXX_MACRO_BODY_DEPENDENCIES_H_

/**** IWYU_SUMMARY
tests/cxx/macro_body_dependencies.h should add these lines:

tests/cxx/macro_body_dependencies.h should remove these lines:
- #include "tests/cxx/macro_body_dependencies-ambiguous-a.h"  // lines XX-XX
- #include "tests/cxx/macro_body_dependencies-ambiguous-b.h"  // lines XX-XX
- #include "tests/cxx/macro_body_dependencies-disabled.h"  // lines XX-XX
- #include "tests/cxx/macro_body_dependencies-member.h"  // lines XX-XX
- #include "tests/cxx/macro_body_dependencies-unrelated.h"  // lines XX-XX

The full include-list for tests/cxx/macro_body_dependencies.h:
#include "tests/cxx/macro_body_dependencies-conditional.h"  // lines XX-XX
#include "tests/cxx/macro_body_dependencies-free.h"  // lines XX-XX
#include "tests/cxx/macro_body_dependencies-nested.h"  // for MACRO_BODY_INNER
#include "tests/cxx/macro_body_dependencies-provider.h"  // lines XX-XX

***** IWYU_SUMMARY */
