#ifndef TESTS_COMPLETE_USE_REFERENCE_MEMBER_ACCESS_HPP_
#define TESTS_COMPLETE_USE_REFERENCE_MEMBER_ACCESS_HPP_

#include "provider.hpp"

inline int ReadReference(ReferenceMemberAccess& value) {
  return value.member;
}

#endif  // TESTS_COMPLETE_USE_REFERENCE_MEMBER_ACCESS_HPP_
