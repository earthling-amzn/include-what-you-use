//===--- fwd004_mapped_deep.cc - provider order test ----------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#include "tests/fwd_order/fwd004_mapped_deep_public.hpp"  // IWYU pragma: keep
class Fwd004MappedDeep;
Fwd004MappedDeep* Fwd004MappedDeepPass(Fwd004MappedDeep* value);
