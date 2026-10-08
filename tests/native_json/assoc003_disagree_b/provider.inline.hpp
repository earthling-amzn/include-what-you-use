#ifndef ASSOC003_DISAGREE_B_PROVIDER_INLINE_HPP
#define ASSOC003_DISAGREE_B_PROVIDER_INLINE_HPP
#include "provider.hpp"
inline int Assoc003DisagreeB::Get() const { return 2; }
inline int ReadAssoc003DisagreeB(const Assoc003DisagreeB& value) {
  return value.Get();
}
#endif
