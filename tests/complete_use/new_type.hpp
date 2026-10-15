#ifndef TESTS_COMPLETE_USE_NEW_TYPE_HPP_
#define TESTS_COMPLETE_USE_NEW_TYPE_HPP_

#include "provider.hpp"

inline NewType* MakeNewType() {
  return new NewType;
}

#endif  // TESTS_COMPLETE_USE_NEW_TYPE_HPP_
