#include "tests/native_json/assoc003_provider_wrapper.hpp"

int ReadAssoc003Provider(const Assoc003PairProvider& value) {
  return value.Get() + ReadAssoc003Pair(value);
}
