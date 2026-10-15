#ifndef TESTS_COMPLETE_USE_ACCESSOR_DEREFERENCE_HPP_
#define TESTS_COMPLETE_USE_ACCESSOR_DEREFERENCE_HPP_

#include "provider.hpp"

AccessorDereference* GetAccessorValue();
inline int ReadAccessor() {
  return GetAccessorValue()->member;
}

#endif  // TESTS_COMPLETE_USE_ACCESSOR_DEREFERENCE_HPP_
