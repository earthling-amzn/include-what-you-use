#ifndef IWYU_TESTS_FWD_ORDER_INLINE_CONDITIONAL_PROVIDER_HPP_
#define IWYU_TESTS_FWD_ORDER_INLINE_CONDITIONAL_PROVIDER_HPP_
class Fwd006UnconditionalClass {};
#if FWD006_ENABLE_CONDITIONAL
class Fwd006ConditionalClass {};
enum class Fwd006ConditionalMode : int { Enabled };
#endif
#endif
