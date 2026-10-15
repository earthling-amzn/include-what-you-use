#ifndef TESTS_COMPLETE_USE_POINTER_DEREFERENCE_HPP_
#define TESTS_COMPLETE_USE_POINTER_DEREFERENCE_HPP_

#include "provider.hpp"

inline int ReadPointer(PointerDereference* value) {
  return value->member;
}

#endif  // TESTS_COMPLETE_USE_POINTER_DEREFERENCE_HPP_
