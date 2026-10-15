#ifndef TESTS_COMPLETE_USE_POINTER_TEMPLATE_ARGUMENT_HPP_
#define TESTS_COMPLETE_USE_POINTER_TEMPLATE_ARGUMENT_HPP_

class PointerTemplateArgument;

template <typename T>
struct Box {};

Box<PointerTemplateArgument*> pointer_box;

#endif  // TESTS_COMPLETE_USE_POINTER_TEMPLATE_ARGUMENT_HPP_
