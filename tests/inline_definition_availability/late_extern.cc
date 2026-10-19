#include "templates.hpp"

int UseBeforeExtern() { return TemplateFunction(1); }
extern template int TemplateFunction<int>(int);

int main() { return UseBeforeExtern(); }
