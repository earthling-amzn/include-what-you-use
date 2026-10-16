#ifndef IWYU_TESTS_FWD_ORDER_MAPPED_DYNAMIC_PUBLIC_HPP_
#define IWYU_TESTS_FWD_ORDER_MAPPED_DYNAMIC_PUBLIC_HPP_
class Fwd004Public {};
#ifdef FWD004_ENABLE_HIDDEN
#include "tests/fwd_order/fwd004_mapped_dynamic_private.hpp"
#endif
#endif
