//===--- projected_use_test.cc - structured-use canonicalization ----------===//

#include "iwyu_projected_use.h"

#include <cstdint>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {

using include_what_you_use::internal::CanonicalJson;
using include_what_you_use::internal::SortAndDeduplicateProjectedUses;
using std::string;
using std::vector;

llvm::json::Object Unavailable() {
  return llvm::json::Object{{"status", "unavailable"}, {"value", nullptr}};
}

llvm::json::Object Condition(const string& status,
                             const string& expression = string()) {
  if (status != "known")
    return llvm::json::Object{{"status", status}, {"value", nullptr}};
  return llvm::json::Object{
      {"status", "known"},
      {"value", llvm::json::Object{
          {"expression", expression},
          {"location", Unavailable()},
          {"active_branch", true},
          {"source_region_id", "region"},
          {"source_region_path", llvm::json::Array{"region"}}}}};
}

llvm::json::Object ProjectedUse(int line, llvm::json::Object condition) {
  return llvm::json::Object{
      {"location", llvm::json::Object{
          {"path", "source.hpp"}, {"line", line}, {"column", 7}}},
      {"requirement", "full"},
      {"origin", "ast"},
      {"condition", std::move(condition)},
      {"macro", Unavailable()}};
}

bool Check(bool condition, const char* message) {
  if (!condition)
    std::cerr << message << '\n';
  return condition;
}

bool CheckCanonicalEscaping() {
  const string expression = string("quote \" slash \\\n\t") + char(1);
  llvm::json::Object value{
      {"source_region_path", llvm::json::Array{"z\n", "a\t"}},
      {"location", Unavailable()},
      {"expression", expression},
      {"source_region_id", "r\"1"},
      {"active_branch", true}};
  const string expected =
      R"({"active_branch":true,"expression":"quote \" slash \\\n\t\u0001","location":{"status":"unavailable","value":null},"source_region_id":"r\"1","source_region_path":["z\n","a\t"]})";
  return Check(CanonicalJson(value) == expected,
               "canonical JSON escaping differs from schema identity");
}

bool CheckProjectionAndConditionOrder() {
  vector<llvm::json::Object> uses;
  uses.push_back(ProjectedUse(3, Condition("unknown")));
  uses.push_back(ProjectedUse(3, Condition("known", "z")));
  uses.push_back(ProjectedUse(3, Condition("not_applicable")));
  uses.push_back(ProjectedUse(3, Condition("known", "a")));
  // Model distinct raw SourceLocation/condition facts whose directive
  // locations both project to unavailable and therefore become identical.
  uses.push_back(ProjectedUse(3, Condition("known", "a")));

  llvm::json::Array result =
      SortAndDeduplicateProjectedUses(std::move(uses));
  if (!Check(result.size() == 4,
             "lossy projected duplicate was not removed"))
    return false;
  vector<string> order;
  for (const llvm::json::Value& value : result) {
    const llvm::json::Object* use = value.getAsObject();
    const llvm::json::Object* condition = use->getObject("condition");
    const string status = condition->getString("status")->str();
    if (status == "known") {
      order.push_back(status + ":" + condition->getObject("value")
                                            ->getString("expression")->str());
    } else {
      order.push_back(status);
    }
  }
  return Check(order == vector<string>({"known:a", "known:z",
                                        "not_applicable", "unknown"}),
               "projected use conditions do not follow schema sort order");
}

bool CheckFiveThousandUses() {
  vector<llvm::json::Object> uses;
  uses.reserve(10000);
  for (int line = 5000; line >= 1; --line) {
    const string status = line % 2 ? "known" : "not_applicable";
    uses.push_back(ProjectedUse(line, Condition(status, "x")));
    uses.push_back(ProjectedUse(line, Condition(status, "x")));
  }
  llvm::json::Array result =
      SortAndDeduplicateProjectedUses(std::move(uses));
  if (!Check(result.size() == 5000,
             "5,000-use group has an incorrect deduplicated size"))
    return false;
  for (size_t index = 0; index < result.size(); ++index) {
    const llvm::json::Object* use = result[index].getAsObject();
    const auto line = use->getObject("location")->getInteger("line");
    if (!line || *line != static_cast<int64_t>(index + 1))
      return Check(false, "5,000-use group is not sorted by location");
  }
  return true;
}

}  // namespace

int main() {
  if (!CheckCanonicalEscaping() || !CheckProjectionAndConditionOrder() ||
      !CheckFiveThousandUses()) {
    return 1;
  }
  std::cout << "projected-use canonicalization contracts passed\n";
  return 0;
}
