#ifndef TESTS_COMPLETE_USE_INCLUDE_AFTER_USE_HPP_
#define TESTS_COMPLETE_USE_INCLUDE_AFTER_USE_HPP_

class IncludeAfterUse;
void TakeBeforeProvider(IncludeAfterUse value);

#include "include_after_use_provider.hpp"

#endif  // TESTS_COMPLETE_USE_INCLUDE_AFTER_USE_HPP_
