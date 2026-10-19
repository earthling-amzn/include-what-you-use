#include "default_provider.hpp"
using DefaultResult = decltype(DefaultedCall());
template<class T> int NeverInstantiatedDefault() { return DefaultedCall(); }
int main() { return DefaultedCall(0); }
