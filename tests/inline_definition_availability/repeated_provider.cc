#if FEATURE
#include "repeat_first.hpp"
#endif
#include "repeat_second.hpp"

int main() { return TemplateFunction(4) == 5 ? 0 : 1; }
