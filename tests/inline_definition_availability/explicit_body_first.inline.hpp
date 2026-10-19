#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_EXPLICIT_BODY_FIRST_INLINE_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_EXPLICIT_BODY_FIRST_INLINE_HPP_

#include "explicit_body_first.hpp"

template <typename T>
int ExplicitBodyFirst(T value) {
  return static_cast<int>(value) + 6;
}

#endif
