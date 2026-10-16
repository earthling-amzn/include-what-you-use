#ifndef TESTS_SEM005_VARIANT_PROVIDER_HPP_
#define TESTS_SEM005_VARIANT_PROVIDER_HPP_

namespace sem005_corpus {

#if defined(SEM005_DEBUG_CONFIGURATION)
class Handle {
 public:
  int value;
};
class AliasHandle {
 public:
  int value;
};
#else
struct Payload {
  int value;
};
typedef Payload* Handle;
using AliasHandle = Payload*;
#endif

}  // namespace sem005_corpus

#endif  // TESTS_SEM005_VARIANT_PROVIDER_HPP_
