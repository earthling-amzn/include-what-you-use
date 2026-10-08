// IWYU_ARGS: -I .

#include "tests/cxx/assoc003_cpp_pair.hpp"
#include "tests/cxx/assoc003_cpp_pair.inline.hpp"

int UseAssoc003CppPair(const Assoc003CppPair& value) {
  return value.Get() + ReadAssoc003CppPair(value);
}

/**** IWYU_SUMMARY

tests/cxx/assoc003_cpp_pair.cc should add these lines:

tests/cxx/assoc003_cpp_pair.cc should remove these lines:
- #include "tests/cxx/assoc003_cpp_pair.hpp"  // lines XX-XX

The full include-list for tests/cxx/assoc003_cpp_pair.cc:
#include "tests/cxx/assoc003_cpp_pair.inline.hpp"  // for Assoc003CppPair, Assoc003CppPair::Get, ReadAssoc003CppPair

***** IWYU_SUMMARY */
