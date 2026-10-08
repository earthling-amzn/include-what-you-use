#ifndef ASSOC003_CPP_PAIR_INLINE_HPP
#define ASSOC003_CPP_PAIR_INLINE_HPP
#include "tests/cxx/assoc003_cpp_pair.hpp"
inline int Assoc003CppPair::Get() const { return 1; }
inline int ReadAssoc003CppPair(const Assoc003CppPair& value) {
  return value.Get();
}
#endif

/**** IWYU_SUMMARY

(tests/cxx/assoc003_cpp_pair.inline.hpp has correct #includes/fwd-decls)

***** IWYU_SUMMARY */
