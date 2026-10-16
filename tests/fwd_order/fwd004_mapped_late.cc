//===--- fwd004_mapped_late.cc - provider order test ----------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

class Fwd004Mapped;
Fwd004Mapped* Fwd004MappedLatePass(Fwd004Mapped* value);
#include "tests/fwd_order/fwd004_mapped_public.hpp"  // IWYU pragma: keep
