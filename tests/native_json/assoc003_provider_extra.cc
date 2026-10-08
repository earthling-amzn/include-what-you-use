#include "tests/native_json/assoc003_provider_wrapper.hpp"
#include "tests/native_json/assoc003_extra_provider.hpp"

int ReadAssoc003ProviderExtra(const Assoc003PairProvider& value) {
  return value.Get() + ReadAssoc003Pair(value);
}
