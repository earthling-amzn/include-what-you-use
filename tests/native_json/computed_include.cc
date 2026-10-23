#define NATIVE_JSON_COMPUTED_PROVIDER(path) \
  "tests/native_json/computed_provider.h"
#define CPU_HEADER(path) NATIVE_JSON_COMPUTED_PROVIDER(path)

  # include /* operand */ CPU_HEADER( gc/shenandoah/computedOne ) // physical one
#include CPU_HEADER(gc/shenandoah/computedTwo) /* physical two */

NativeJsonComputedProvider native_json_computed_provider;
