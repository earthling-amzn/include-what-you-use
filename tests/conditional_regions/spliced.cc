#include "tests/conditional_regions/all.hpp"

#\
if defined(SPLICED_BRANCH) && \
    1
int Mc002SplicedValue(Mc002Jit* value) {
  return value->value;
}
#\
else
int Mc002SplicedValue(Mc002Generic* value) {
  return value->value;
}
#\
endif

/*
#if COMMENT_ONLY
#else
#endif
*/
const char* mc002_spliced_text = "#if STRING_ONLY";
const char* mc002_spliced_raw = R"mc002(
#if RAW_STRING_ONLY
#else
#endif
)mc002";
