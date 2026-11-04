//===--- re_fwd_decl.cc - test input file for iwyu ------------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

// IWYU_ARGS: -I .

// If we use a class in a way that only requires a forward-declaration,
// and the class is defined in some file we directly #include, we don't
// need the forward-decl here. A declaration from one exact direct include also
// makes a source-file-local duplicate unnecessary; published headers still own
// their local declaration.

#include "tests/cxx/re_fwd_decl.h"
#include "tests/cxx/re_fwd_decl-d1.h"

Direct* d = 0;
Indirect* i = 0;
// This is not an iwyu violation because it's declared in the .h file
DeclaredInH* dih = 0;

// This forces us to #include re_fwd_decl-d1.h.  Without this, iwyu would
// just suggest replacing the #include with 'class Direct'.
FullUse fu;

/**** IWYU_SUMMARY

(tests/cxx/re_fwd_decl.cc has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
