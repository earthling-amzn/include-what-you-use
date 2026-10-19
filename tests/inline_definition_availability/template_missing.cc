#include "templates.hpp"

int main() {
  TemplateBox<int> value;
  auto function_address = &TemplateFunction<int>;
  auto member_pointer = &TemplateBox<int>::AddressMember;
  return function_address(2) + value.Member() +
         (value.*member_pointer)() + value.MemberTemplate(3) +
         InstantiateDependent(value);
}
