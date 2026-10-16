#include "api.inline.hpp"

int main() {
  InlineLifecycle value;
  return value.Member() + InlineFree(1) == 12 ? 0 : 1;
}
