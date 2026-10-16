//===--- fwd004_mapped_ambiguous.cc - provider order test -----------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#include "tests/fwd_order/fwd004_mapped_ambiguous_public.hpp"  // IWYU pragma: keep
class Fwd004MappedAmbiguous;
Fwd004MappedAmbiguous* Fwd004MappedAmbiguousPass(
    Fwd004MappedAmbiguous* value);
