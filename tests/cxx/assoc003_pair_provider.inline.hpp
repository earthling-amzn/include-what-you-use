#ifndef ASSOC003_PAIR_PROVIDER_INLINE_HPP
#define ASSOC003_PAIR_PROVIDER_INLINE_HPP
#include "tests/cxx/assoc003_pair_provider.hpp"
inline int Assoc003PairProvider::Get() const { return 1; }
inline int ReadAssoc003Pair(const Assoc003PairProvider& value) {
  return value.Get();
}
inline int Assoc003SoleInline() { return 3; }
#endif
