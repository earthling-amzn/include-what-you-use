#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_EXPLICIT_MAPPED_INLINE_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_EXPLICIT_MAPPED_INLINE_HPP_

template <typename T>
int ExplicitMapped(T value) {
  return static_cast<int>(value) + 8;
}

extern template int ExplicitMapped<int>(int);

#endif
