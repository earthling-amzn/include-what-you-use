#include "tests/conditional_regions/non_file_provider.hpp"

class NonFileConditionalConsumer {
#if defined(NON_FILE_CONDITION)
  NonFileConditionalType* value;
#endif
};
