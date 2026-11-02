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
  AllowAdd,
  AllowRemove,
  DenyAdd,
  DenyRemove,
  Keep,
  Associate,
  Export,
  PreferProvider,
  ReportOnly,
};

enum class ProjectPolicyActionFamily {
  Addition,
  Removal,
};

struct ProjectPolicyDecision {
  ProjectPolicyAction action;
  string policy_file;
  string rule_id;
};

enum class ProjectPolicyConditionAvailability {
  NotApplicable,
  Known,
  Unknown,
};

// Expression is populated only when availability is Known.
struct ProjectPolicyActiveCondition {
  ProjectPolicyConditionAvailability availability =
      ProjectPolicyConditionAvailability::Unknown;
  string expression;
};

enum class ProjectPolicyLexicalScopeAvailability {
  NotApplicable,
  Known,
  Unknown,
};

// Scope is populated only when availability is Known.
struct ProjectPolicyEditLexicalScope {
  ProjectPolicyLexicalScopeAvailability availability =
      ProjectPolicyLexicalScopeAvailability::Unknown;
  string scope;
};

enum class ProjectPolicyReplacementAvailability {
  NotApplicable,
  Known,
  Unknown,
};

// Replacement is populated only when availability is Known.
struct ProjectPolicyReplacement {
  ProjectPolicyReplacementAvailability availability =
      ProjectPolicyReplacementAvailability::Unknown;
  string replacement;
};

// Decision is populated only for Match. The indeterminate fields identify the
// rule where ordered lookup stopped, but do not represent a matched decision.
struct ProjectPolicyMatchResult {
  enum class Kind {
    NoMatch,
    Match,
    Indeterminate,
  };

  Kind kind = Kind::NoMatch;
  std::optional<ProjectPolicyDecision> decision;
  string indeterminate_policy_file;
  string indeterminate_rule_id;
  string indeterminate_fact;
};

// Runtime facts supplied by the analyzer. Paths use '/' separators. Includee
// spellings do not include quote or angle-bracket delimiters.
struct ProjectPolicyMatchContext {
  string includer;
  string includee;
  string file_kind;
  ProjectPolicyActiveCondition active_condition;
  ProjectPolicyEditLexicalScope edit_lexical_scope;
  ProjectPolicyReplacement replacement;
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
                                   bool has_structured_schema_v2,
                                   string* error) const;

  ProjectPolicyMatchResult FirstMatchingRule(
      ProjectPolicyAction action,
      const ProjectPolicyMatchContext& context) const;
  ProjectPolicyMatchResult FirstMatchingRule(
      ProjectPolicyActionFamily family,
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
