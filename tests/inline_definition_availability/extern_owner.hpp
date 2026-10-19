#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_EXTERN_OWNER_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_EXTERN_OWNER_HPP_

#include "templates.hpp"

extern template int TemplateFunction<int>(int);
extern template struct TemplateBox<int>;

#endif
