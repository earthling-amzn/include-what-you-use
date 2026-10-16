#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_API_INLINE_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_API_INLINE_HPP_

#include "api.hpp"

inline InlineLifecycle::InlineLifecycle() = default;
inline InlineLifecycle::~InlineLifecycle() = default;
inline int InlineLifecycle::Member() const { return 10; }
inline int InlineLifecycle::AddressMember() const { return 30; }
inline int InlineFree(int value) { return value + 1; }
inline int InlineAddress(int value) { return value + 20; }

template <>
inline int InlineTemplate<int>(int value) {
  return value + 40;
}

#endif
