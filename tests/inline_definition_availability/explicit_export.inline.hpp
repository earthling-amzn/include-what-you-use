#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_EXPLICIT_EXPORT_INLINE_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_EXPLICIT_EXPORT_INLINE_HPP_

template <typename T>
int ExplicitExport(T value) {
  return static_cast<int>(value) + 7;
}

extern template int ExplicitExport<int>(int);

#endif
