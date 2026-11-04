#ifndef IWYU_TESTS_FWD_ORDER_HEADER_OWNER_INLINE_HPP_
#define IWYU_TESTS_FWD_ORDER_HEADER_OWNER_INLINE_HPP_
#include "tests/fwd_order/fwd006_header_owner.hpp"
class Fwd006HeaderAssociated;
Fwd006HeaderAssociated* Fwd006HeaderAssociatedPass(
    Fwd006HeaderAssociated* value);
struct Fwd006HeaderAssociatedHolder {
  Fwd006HeaderAssociatedAnchor anchor;
};
#endif
