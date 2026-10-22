//===--- iwyu_projected_use.h - canonical structured uses -------*- C++ -*-===//

#ifndef INCLUDE_WHAT_YOU_USE_IWYU_PROJECTED_USE_H_
#define INCLUDE_WHAT_YOU_USE_IWYU_PROJECTED_USE_H_

#include <string>
#include <vector>

#include "llvm/Support/JSON.h"

namespace include_what_you_use {
namespace internal {

std::string CanonicalJson(const llvm::json::Value& value);
std::string CanonicalJson(const llvm::json::Object& object);
std::string CanonicalJson(const llvm::json::Array& array);

// Sort and deduplicate completed schema use objects by the exact identity used
// by iwyu_tool.py::_use_sort_key. Inputs must already satisfy the use schema.
llvm::json::Array SortAndDeduplicateProjectedUses(
    std::vector<llvm::json::Object> uses);

}  // namespace internal
}  // namespace include_what_you_use

#endif  // INCLUDE_WHAT_YOU_USE_IWYU_PROJECTED_USE_H_
