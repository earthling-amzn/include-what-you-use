#include "api.hpp"

int main() {
  InlineLifecycle value;
  auto* function_address = &InlineAddress;
  auto member_pointer = &InlineLifecycle::AddressMember;
  return value.Member() + InlineFree(1) + function_address(2) +
         (value.*member_pointer)() + InlineTemplate<int>(3);
}
