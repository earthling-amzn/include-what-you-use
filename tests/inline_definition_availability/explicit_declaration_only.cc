#include "explicit_body_first.hpp"  // IWYU pragma: keep
#include "explicit_body_first.inline.hpp"

extern template int ExplicitBodyFirst<int>(int);

int main() {
  return 0;
}
