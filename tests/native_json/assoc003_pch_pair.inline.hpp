#ifndef ASSOC003_PCH_PAIR_INLINE_HPP
#define ASSOC003_PCH_PAIR_INLINE_HPP
#include "tests/native_json/assoc003_pch_pair.hpp"
inline int Assoc003PchPair::Get() const { return 1; }
inline int ReadAssoc003PchPair(const Assoc003PchPair& value) {
  return value.Get();
}
#endif
