//===--- iwyu_projected_use.cc - canonical structured uses ---------------===//

#include "iwyu_projected_use.h"

#include <algorithm>
#include <cstdint>
#include <map>
#include <tuple>
#include <utility>
#include <vector>

#include "iwyu_port.h"
#include "llvm/Support/raw_ostream.h"

namespace include_what_you_use {
namespace internal {

using llvm::raw_string_ostream;
using std::map;
using std::string;
using std::vector;

// Match the schema's canonical JSON identity (sorted object keys, compact
// separators, UTF-8 strings). This is intentionally derived from the final
// projected value: SourceLocations and ActiveConditions contain distinctions
// that can disappear when their schema objects are constructed.
string CanonicalJson(const llvm::json::Object& object);
string CanonicalJson(const llvm::json::Array& array);

string CanonicalJson(const llvm::json::Value& value) {
  if (const llvm::json::Object* object = value.getAsObject())
    return CanonicalJson(*object);
  if (const llvm::json::Array* array = value.getAsArray())
    return CanonicalJson(*array);
  string result;
  raw_string_ostream stream(result);
  stream << value;
  stream.flush();
  return result;
}

string CanonicalJson(const llvm::json::Object& object) {
  vector<std::pair<string, const llvm::json::Value*>> properties;
  properties.reserve(object.size());
  for (const auto& property : object)
    properties.emplace_back(property.first.str(), &property.second);
  std::sort(properties.begin(), properties.end(),
            [](const auto& lhs, const auto& rhs) {
              return lhs.first < rhs.first;
            });
  string result = "{";
  for (size_t index = 0; index < properties.size(); ++index) {
    if (index != 0)
      result += ',';
    string key;
    raw_string_ostream key_stream(key);
    key_stream << llvm::json::Value(properties[index].first);
    key_stream.flush();
    result += key;
    result += ':';
    result += CanonicalJson(*properties[index].second);
  }
  result += '}';
  return result;
}

string CanonicalJson(const llvm::json::Array& array) {
  string result = "[";
  for (size_t index = 0; index < array.size(); ++index) {
    if (index != 0)
      result += ',';
    result += CanonicalJson(array[index]);
  }
  result += ']';
  return result;
}

llvm::json::Array SortAndDeduplicateProjectedUses(
    vector<llvm::json::Object> uses) {
  using SortIdentity = std::tuple<string, int64_t, int64_t, int, int,
                                  string, string, string, string>;
  struct ProjectedUse {
    llvm::json::Object value;
    SortIdentity identity;
  };
  const map<string, int> origin_rank = {
      {"ast", 0}, {"macro", 1}, {"heuristic", 2}, {"unknown", 3}};
  const map<string, int> requirement_rank = {
      {"full", 0}, {"forward_declaration", 1}, {"none", 2}, {"unknown", 3}};
  vector<ProjectedUse> projected;
  projected.reserve(uses.size());
  for (llvm::json::Object& use : uses) {
    const llvm::json::Object* location = use.getObject("location");
    const llvm::json::Object* condition = use.getObject("condition");
    const llvm::json::Object* macro = use.getObject("macro");
    const auto origin = use.getString("origin");
    const auto requirement = use.getString("requirement");
    CHECK_(location && condition && macro && origin && requirement);
    const auto path = location->getString("path");
    const auto line = location->getInteger("line");
    const auto column = location->getInteger("column");
    const auto condition_status = condition->getString("status");
    const auto macro_status = macro->getString("status");
    CHECK_(path && line && column && condition_status && macro_status);
    projected.push_back({
        std::move(use),
        std::make_tuple(path->str(), *line, *column,
                        origin_rank.at(origin->str()),
                        requirement_rank.at(requirement->str()),
                        condition_status->str(), CanonicalJson(*condition),
                        macro_status->str(), CanonicalJson(*macro))});
  }
  std::sort(projected.begin(), projected.end(),
            [](const ProjectedUse& lhs, const ProjectedUse& rhs) {
              return lhs.identity < rhs.identity;
            });
  llvm::json::Array result;
  bool have_previous = false;
  SortIdentity previous;
  for (ProjectedUse& use : projected) {
    if (have_previous && use.identity == previous)
      continue;
    have_previous = true;
    previous = use.identity;
    result.push_back(std::move(use.value));
  }
  return result;
}

}  // namespace internal
}  // namespace include_what_you_use
