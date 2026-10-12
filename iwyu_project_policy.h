//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#ifndef INCLUDE_WHAT_YOU_USE_IWYU_PROJECT_POLICY_H_
#define INCLUDE_WHAT_YOU_USE_IWYU_PROJECT_POLICY_H_

#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace include_what_you_use {

using std::string;
using std::vector;

enum class ProjectPolicyAction {
  DenyAdd,
  DenyRemove,
  Keep,
  Associate,
  Export,
  PreferProvider,
  ReportOnly,
};

struct ProjectPolicyDecision {
  ProjectPolicyAction action;
  string policy_file;
  string rule_id;
};

// Runtime facts supplied by the analyzer. Paths use '/' separators. Includee
// spellings do not include quote or angle-bracket delimiters.
struct ProjectPolicyMatchContext {
  string includer;
  string includee;
  string file_kind;
  string active_condition;
  vector<string> symbols;
  string configuration;
};

class ProjectPolicy {
 public:
  ~ProjectPolicy();

  ProjectPolicy(const ProjectPolicy&) = delete;
  ProjectPolicy& operator=(const ProjectPolicy&) = delete;

  static std::unique_ptr<ProjectPolicy> Load(const string& path,
                                             string* error);

  // Rejects recognized rules that this migration phase cannot safely apply.
  bool ValidateRuntimeCapabilities(bool has_configuration,
                                   string* error) const;

  std::optional<ProjectPolicyDecision> FirstMatchingRule(
      ProjectPolicyAction action,
      const ProjectPolicyMatchContext& context) const;

  const string& path() const { return path_; }

 private:
  class Rule;

  ProjectPolicy() = default;

  string path_;
  vector<std::unique_ptr<Rule>> rules_;
};

// Shared with structured output so policy and output classify the same path.
string ProjectPolicyFileKind(const string& path);

}  // namespace include_what_you_use

#endif  // INCLUDE_WHAT_YOU_USE_IWYU_PROJECT_POLICY_H_
