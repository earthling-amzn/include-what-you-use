#pragma once

#include "ambiguity_member_api.hpp"

template <class T>
int AmbiguousMemberBox<T>::Second(T lhs, T rhs) {
  return lhs + rhs;
}
