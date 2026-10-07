#ifndef INCLUDE_WHAT_YOU_USE_TESTS_CXX_CONDITIONAL_INLINE_EXPORT_PROVIDER_INLINE_HPP_
#define INCLUDE_WHAT_YOU_USE_TESTS_CXX_CONDITIONAL_INLINE_EXPORT_PROVIDER_INLINE_HPP_
#if ENABLE_EXPORT
#include "tests/cxx/conditional_inline_export_provider.hpp"
#endif
inline int ConditionalInlineExportProvider::Get() const { return 1; }
#endif
