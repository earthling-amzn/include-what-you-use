#include "explicit_mapped_carrier.hpp"

template int ExplicitMapped<int>(int);

int main() {
  return ExplicitMapped(1) == 9 ? 0 : 1;
}
