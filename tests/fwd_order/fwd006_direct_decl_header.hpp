#ifndef IWYU_TESTS_FWD_ORDER_DIRECT_DECL_HEADER_HPP_
#define IWYU_TESTS_FWD_ORDER_DIRECT_DECL_HEADER_HPP_
#include "tests/fwd_order/fwd006_direct_decl_provider.hpp"
class Fwd006DirectDeclared;
Fwd006DirectDeclared* Fwd006HeaderPass(Fwd006DirectDeclared* value);
struct Fwd006HeaderHolder { Fwd006DirectAnchor anchor; };
#endif
