#include "tests/native_json/assoc003_disagree_a/wrapper.hpp"
#include "tests/native_json/assoc003_disagree_b/wrapper.hpp"

int ReadAssoc003Disagreement(const Assoc003DisagreeA& a,
                             const Assoc003DisagreeB& b) {
  return a.Get() + ReadAssoc003DisagreeA(a) +
         b.Get() + ReadAssoc003DisagreeB(b);
}
