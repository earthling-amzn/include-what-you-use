#ifndef TESTS_COMPLETE_USE_CONDITIONAL_PROVIDER_HPP_
#define TESTS_COMPLETE_USE_CONDITIONAL_PROVIDER_HPP_

class ConditionalProviderValue;

#if FLAG
#include "conditional_provider_definition.hpp"
#endif

void TakeConditionalProvider(ConditionalProviderValue value);

#endif  // TESTS_COMPLETE_USE_CONDITIONAL_PROVIDER_HPP_
