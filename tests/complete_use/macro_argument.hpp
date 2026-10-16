#ifndef TESTS_COMPLETE_USE_MACRO_ARGUMENT_HPP_
#define TESTS_COMPLETE_USE_MACRO_ARGUMENT_HPP_

#include "macro_provider.hpp"

#define DECL_MACRO_ARGUMENT(type) void MacroArgument(type value)

DECL_MACRO_ARGUMENT(MacroArgumentValue);

#endif  // TESTS_COMPLETE_USE_MACRO_ARGUMENT_HPP_
