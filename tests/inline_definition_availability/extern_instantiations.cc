#include "template_bodies.hpp"

template int TemplateFunction<int>(int);
template struct TemplateBox<int>;

int UseAfterExplicitInstantiation() {
  TemplateBox<int> value;
  return TemplateFunction(2) + value.Member();
}
