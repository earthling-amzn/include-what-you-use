#include "templates.hpp"

template <typename T>
using UnevaluatedTemplate = decltype(TemplateFunction(T{}));

template <typename T>
int DiscardedTemplate(T value) {
  if constexpr (sizeof(T) == 0)
    return TemplateFunction(value);
  return 0;
}

int main() { return 0; }
