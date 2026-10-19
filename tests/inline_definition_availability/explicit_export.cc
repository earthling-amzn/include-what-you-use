#include "explicit_export_carrier.hpp"

template int ExplicitExport<int>(int);

int main() {
  return ExplicitExport(1) == 8 ? 0 : 1;
}
