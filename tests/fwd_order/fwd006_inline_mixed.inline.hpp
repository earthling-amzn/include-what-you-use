#ifndef IWYU_TESTS_FWD_ORDER_INLINE_MIXED_INLINE_HPP_
#define IWYU_TESTS_FWD_ORDER_INLINE_MIXED_INLINE_HPP_
#include "tests/fwd_order/fwd006_inline_mixed.hpp"
class Fwd006InlineClass;
Fwd006InlineClass* Fwd006InlineMixedPass(Fwd006InlineClass* value);
struct Fwd006InlineMixedHolder { Fwd006InlineMixedAnchor anchor; };
#endif
