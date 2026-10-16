#ifndef IWYU_TESTS_FWD_ORDER_FWD003_EARLY_HPP_
#define IWYU_TESTS_FWD_ORDER_FWD003_EARLY_HPP_
class Fwd003Value;
#include "tests/fwd_order/fwd003_later.hpp"  // IWYU pragma: keep
Fwd003Value* Fwd003Pass(Fwd003Value* value);
#endif
