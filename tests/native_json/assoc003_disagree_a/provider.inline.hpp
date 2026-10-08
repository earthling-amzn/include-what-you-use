#ifndef ASSOC003_DISAGREE_A_PROVIDER_INLINE_HPP
#define ASSOC003_DISAGREE_A_PROVIDER_INLINE_HPP
#include "provider.hpp"
inline int Assoc003DisagreeA::Get() const { return 1; }
inline int ReadAssoc003DisagreeA(const Assoc003DisagreeA& value) {
  return value.Get();
}
#endif
