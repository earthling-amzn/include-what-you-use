#include "templates.hpp"

#if FEATURE
extern template int TemplateFunction<int>(int);
#endif

int main() { return TemplateFunction(1); }
