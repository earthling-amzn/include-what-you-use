#ifndef IWYU_TESTS_FWD_ORDER_INLINE_CLASS_INLINE_HPP_
#define IWYU_TESTS_FWD_ORDER_INLINE_CLASS_INLINE_HPP_
#include "tests/fwd_order/fwd006_inline_class.hpp"
class Fwd006InlineClass;
Fwd006InlineClass* Fwd006InlineClassPass(Fwd006InlineClass* value);
struct Fwd006InlineClassHolder { Fwd006InlineClassOwnerAnchor anchor; };
#endif
