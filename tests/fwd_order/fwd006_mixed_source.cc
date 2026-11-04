#include "tests/fwd_order/fwd006_mixed_unconditional.hpp"
class Fwd006MixedUnconditional;
#if FWD006_ENABLE_MIXED_SOURCE
#include "tests/fwd_order/fwd006_mixed_conditional.hpp"  // IWYU pragma: keep
#endif
class Fwd006MixedConditional;
Fwd006MixedUnconditional* Fwd006MixedUnconditionalPass(Fwd006MixedUnconditional*);
Fwd006MixedConditional* Fwd006MixedConditionalPass(Fwd006MixedConditional*);
Fwd006MixedAnchor fwd006_mixed_anchor;
