#include "tests/conditional_regions/all.hpp"

#if defined(COMPILER2)
int Mc002CompilerValue(Mc002Jit* value) {
  return value->value;
}
#elif defined(ZERO)
int Mc002CompilerValue(Mc002Zero* value) {
  return value->value;
}
#else
int Mc002CompilerValue(Mc002Generic* value) {
  return value->value;
}
#endif

#if defined(ARCH_TEST)
#if defined(X86)
int Mc002ArchitectureValue(Mc002X86* value) {
  return value->value;
}
#else
int Mc002ArchitectureValue(Mc002Aarch64* value) {
  return value->value;
}
#endif
#endif
