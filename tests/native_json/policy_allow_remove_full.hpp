#ifndef TESTS_NATIVE_JSON_POLICY_ALLOW_REMOVE_FULL_HPP_
#define TESTS_NATIVE_JSON_POLICY_ALLOW_REMOVE_FULL_HPP_

#include "tests/native_json/policy_allow_remove_provider.inline.hpp"

inline int UsePolicyAllowRemoveDefinition(PolicyAllowRemoveType* value) {
  return value->value();
}

#endif  // TESTS_NATIVE_JSON_POLICY_ALLOW_REMOVE_FULL_HPP_
