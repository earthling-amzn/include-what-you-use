#include "api.hpp"

int main() {
  InlineLifecycle value;
  return value.Member() + InlineFree(1);
}
