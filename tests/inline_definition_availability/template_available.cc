#include "template_bodies.hpp"

int main() {
  TemplateBox<int> automatic;
  TemplateBox<int> temporary;
  auto* heap = new TemplateBox<int>();
  auto function_address = &TemplateFunction<int>;
  auto member_pointer = &TemplateBox<int>::AddressMember;
  const int result = TemplateFunction(1) + function_address(2) +
                     automatic.Member() + (temporary.*member_pointer)() +
                     heap->MemberTemplate(3) + InstantiateDependent(automatic);
  delete heap;
  return result == 78 ? 0 : 1;
}
