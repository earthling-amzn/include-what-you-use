#include "tests/fwd_order/fwd006_direct_decl_provider.hpp"
class Fwd006DirectDeclared;
Fwd006DirectDeclared* Fwd006LatePass(Fwd006DirectDeclared* value);
#include "tests/fwd_order/fwd006_late_decl.hpp"
Fwd006LateOwner fwd006_late_owner;
