#ifndef TESTS_SEM005_VARIANT_PROVIDER_HPP_
#define TESTS_SEM005_VARIANT_PROVIDER_HPP_

#include <vector>

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
enum class EnumClassHandle { value };
class CompatibleRecord {};
#else
struct Payload {
  int value;
};
typedef Payload* Handle;
using AliasHandle = Payload*;
class EnumClassHandle {};
struct CompatibleRecord {};
#endif

}  // namespace sem005_corpus

#endif  // TESTS_SEM005_VARIANT_PROVIDER_HPP_
