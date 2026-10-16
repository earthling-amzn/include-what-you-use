//===--- fwd004_associated_late.cc - provider order test ------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

class Fwd004Associated;
Fwd004Associated* Fwd004AssociatedLatePass(Fwd004Associated* value);
#include "tests/fwd_order/fwd004_associated.hpp"  // IWYU pragma: associated
