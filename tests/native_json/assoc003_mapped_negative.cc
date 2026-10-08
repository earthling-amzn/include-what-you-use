#include "tests/native_json/assoc003_mapped_negative.hpp"
#include "tests/native_json/assoc003_mapped_negative.inline.hpp"
#include "tests/native_json/assoc003_unrelated_trigger.hpp"

int UseAssoc003MappedNegative(const Assoc003MappedNegative& value,
                              Assoc003UnrelatedTrigger trigger) {
  return value.value + trigger.value;
}
