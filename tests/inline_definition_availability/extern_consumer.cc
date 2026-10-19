#include "extern_owner.hpp"

int main() {
  TemplateBox<int> value;
  return TemplateFunction(1) + value.Member() == 12 ? 0 : 1;
}
