//===--- macro_body_dependencies.cc - test --------------------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

// IWYU_ARGS: -Xiwyu --check_also="tests/cxx/macro_body_dependencies-nested.h" \
//            -I .

#include "tests/cxx/macro_body_dependencies.h"

int macro_body_dependencies_main() {
  // The analyzer must preserve the provider for consumers that expand macros
  // later, rather than relying on an expansion in this translation unit.
  return macro_body_dependencies_anchor();
}

/**** IWYU_SUMMARY

(tests/cxx/macro_body_dependencies.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
