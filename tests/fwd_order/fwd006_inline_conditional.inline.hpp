#ifndef IWYU_TESTS_FWD_ORDER_INLINE_CONDITIONAL_INLINE_HPP_
#define IWYU_TESTS_FWD_ORDER_INLINE_CONDITIONAL_INLINE_HPP_
#include "tests/fwd_order/fwd006_inline_conditional.hpp"
class Fwd006UnconditionalClass;
class Fwd006ConditionalClass;
enum class Fwd006ConditionalMode : int;
Fwd006UnconditionalClass* Fwd006UnconditionalPass(Fwd006UnconditionalClass*);
Fwd006ConditionalClass* Fwd006ConditionalPass(Fwd006ConditionalClass*);
Fwd006ConditionalMode* Fwd006ConditionalModePass(Fwd006ConditionalMode*);
struct Fwd006InlineConditionalHolder { Fwd006InlineConditionalAnchor anchor; };
#endif
