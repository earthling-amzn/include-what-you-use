#include "explicit_body_first.inline.hpp"

extern template int ExplicitBodyFirst<int>(int);
template int ExplicitBodyFirst<int>(int);

int main() {
  return ExplicitBodyFirst(5) == 11 ? 0 : 1;
}
