#include "api.hpp"

int UseBeforeDefinition() { return InlineFree(4); }

#include "api.inline.hpp"

int main() { return UseBeforeDefinition() == 5 ? 0 : 1; }
