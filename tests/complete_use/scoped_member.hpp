#ifndef TESTS_COMPLETE_USE_SCOPED_MEMBER_HPP_
#define TESTS_COMPLETE_USE_SCOPED_MEMBER_HPP_

#include "provider.hpp"

inline int ReadScopedMember() {
  return ScopedMember::member;
}

#endif  // TESTS_COMPLETE_USE_SCOPED_MEMBER_HPP_
