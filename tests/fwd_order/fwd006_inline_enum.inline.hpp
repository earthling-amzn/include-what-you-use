#ifndef IWYU_TESTS_FWD_ORDER_INLINE_ENUM_INLINE_HPP_
#define IWYU_TESTS_FWD_ORDER_INLINE_ENUM_INLINE_HPP_
#include "tests/fwd_order/fwd006_inline_enum.hpp"
enum class Fwd006InlineMode : int;
Fwd006InlineMode* Fwd006InlineEnumPass(Fwd006InlineMode* value);
struct Fwd006InlineEnumHolder { Fwd006InlineEnumOwnerAnchor anchor; };
#endif
