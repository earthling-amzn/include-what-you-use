#include "api.inline.hpp"

int main() {
  InlineLifecycle value;
  auto* function_address = &InlineAddress;
  auto member_pointer = &InlineLifecycle::AddressMember;
  const int result = value.Member() + InlineFree(1) + function_address(2) +
                     (value.*member_pointer)() + InlineTemplate<int>(3);
  return result == 107 ? 0 : 1;
}
