#ifndef TESTS_NATIVE_JSON_POLICY_ALLOW_REMOVE_COMPUTED_HPP_
#define TESTS_NATIVE_JSON_POLICY_ALLOW_REMOVE_COMPUTED_HPP_

#define POLICY_ALLOW_REMOVE_PROVIDER \
  "tests/native_json/policy_allow_remove_provider.inline.hpp"
#include POLICY_ALLOW_REMOVE_PROVIDER

void ObserveComputedPolicyAllowRemove(PolicyAllowRemoveType* value);

#endif  // TESTS_NATIVE_JSON_POLICY_ALLOW_REMOVE_COMPUTED_HPP_
