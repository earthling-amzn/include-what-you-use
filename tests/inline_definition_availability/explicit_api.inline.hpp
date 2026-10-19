#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_EXPLICIT_API_INLINE_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_EXPLICIT_API_INLINE_HPP_

#include "explicit_api.hpp"

template <typename T>
int Explicit(T value) {
  return static_cast<int>(value) + 1;
}

template <typename T>
int ExplicitMemberBox<T>::Member(T value) {
  return static_cast<int>(value) + 2;
}

template <typename T>
int ExplicitClassBox<T>::Member(T value) {
  return static_cast<int>(value) + 3;
}

template <typename T>
int ConditionalExplicit(T value) {
  return static_cast<int>(value) + 4;
}

#endif
