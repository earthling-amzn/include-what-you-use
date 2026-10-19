#include "default_provider.hpp"
#include "default_provider.inline.hpp"
template<class T> int InstantiatedDefault() { return DefaultedCall(); }
int main() { return InstantiatedDefault<int>(); }
