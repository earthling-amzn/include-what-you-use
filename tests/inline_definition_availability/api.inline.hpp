#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_API_INLINE_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_API_INLINE_HPP_

#include "api.hpp"

inline InlineLifecycle::InlineLifecycle() = default;
inline InlineLifecycle::~InlineLifecycle() = default;
inline int InlineLifecycle::Member() const { return 10; }
inline int InlineFree(int value) { return value + 1; }

#endif
