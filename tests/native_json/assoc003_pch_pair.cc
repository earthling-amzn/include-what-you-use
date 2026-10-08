#include "tests/native_json/assoc003_pch_pair.hpp"
#include "tests/native_json/assoc003_pch_pair.inline.hpp"

int UseAssoc003PchPair(const Assoc003PchPair& value) {
  return value.Get() + ReadAssoc003PchPair(value);
}
