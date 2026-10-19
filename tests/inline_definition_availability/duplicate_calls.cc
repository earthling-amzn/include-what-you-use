#include "template_bodies.hpp"

int main() {
  return TemplateFunction(1) + TemplateFunction(2) == 5 ? 0 : 1;
}
