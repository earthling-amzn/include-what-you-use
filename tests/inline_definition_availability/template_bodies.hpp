#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_TEMPLATE_BODIES_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_TEMPLATE_BODIES_HPP_

#include "templates.hpp"  // IWYU pragma: export

template <typename T>
int TemplateFunction(T value) {
  return static_cast<int>(value) + 1;
}

template <typename T>
TemplateBox<T>::TemplateBox() = default;

template <typename T>
TemplateBox<T>::~TemplateBox() = default;

template <typename T>
int TemplateBox<T>::Member() const {
  return 10;
}

template <typename T>
int TemplateBox<T>::AddressMember() const {
  return 20;
}

template <typename T>
template <typename U>
int TemplateBox<T>::MemberTemplate(U value) const {
  return static_cast<int>(value) + 30;
}

#endif
