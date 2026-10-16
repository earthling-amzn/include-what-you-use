//===--- fwd004_associated_instance.cc - provider order test --------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#include "tests/fwd_order/fwd004_associated_seed.hpp"
#include "tests/fwd_order/fwd004_associated_instance.hpp"  // IWYU pragma: associated
class Fwd004Associated;
Fwd004Associated* Fwd004AssociatedInstancePass(Fwd004Associated* value);
