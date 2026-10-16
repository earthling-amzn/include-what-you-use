//===--- fwd004_mapped_dynamic.cc - provider order test ------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

class Fwd004Hidden;

#define FWD004_ENABLE_HIDDEN 1
#include "tests/fwd_order/fwd004_mapped_dynamic_private.hpp"

Fwd004Public fwd004_public;
Fwd004Hidden* Fwd004PassHidden(Fwd004Hidden* value);
