#ifndef TESTS_COMPLETE_USE_CONDITIONAL_PROVIDER_HPP_
#define TESTS_COMPLETE_USE_CONDITIONAL_PROVIDER_HPP_

class ConditionalProviderValue;

#define DECL_CONDITIONAL_PROVIDER() \
  void TakeConditionalProvider(ConditionalProviderValue value)

#if FLAG

inline constexpr int ConditionalControl = FLAG;
#endif

DECL_CONDITIONAL_PROVIDER();

#endif  // TESTS_COMPLETE_USE_CONDITIONAL_PROVIDER_HPP_
