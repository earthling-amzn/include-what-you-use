//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#include "iwyu_project_policy.h"

#include <algorithm>
#include <cctype>
#include <map>
#include <memory>
#include <set>
#include <utility>

#include "iwyu_path_util.h"
#include "iwyu_string_util.h"
#include "llvm/ADT/SmallString.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/Support/Error.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/GlobPattern.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/Regex.h"
#include "llvm/Support/SourceMgr.h"
#include "llvm/Support/YAMLParser.h"
#include "llvm/Support/YAMLTraits.h"

namespace include_what_you_use {
namespace {

using llvm::MemoryBuffer;
using llvm::StringRef;
using llvm::yaml::KeyValueNode;
using llvm::yaml::MappingNode;
using llvm::yaml::Node;
using llvm::yaml::ScalarNode;
using llvm::yaml::SequenceNode;

struct RawPattern {
  enum Kind { Glob, Regex } kind;
  string value;
};

struct RawMatch {
  std::optional<RawPattern> includer;
  std::optional<RawPattern> includee;
  std::optional<string> file_kind;
  std::optional<string> active_condition;
  std::optional<string> symbol;
  std::optional<string> configuration;
};

struct RawRule {
  string id;
  string action;
  RawMatch match;
};

struct RawPolicy {
  vector<RawRule> rules;
};

string Context(const string& path, const string& context,
               const string& message) {
  string result = "project policy '" + path + "'";
  if (!context.empty())
    result += " " + context;
  return result + ": " + message;
}

bool Fail(const string& path, const string& context, const string& message,
          string* error) {
  *error = Context(path, context, message);
  return false;
}

bool RejectGraphFeatures(Node* node, const string& path,
                         const string& context, string* error) {
  if (!node)
    return Fail(path, context, "missing YAML node", error);
  if (!node->getAnchor().empty())
    return Fail(path, context, "anchors are not allowed", error);
  if (!node->getRawTag().empty())
    return Fail(path, context, "explicit tags are not allowed", error);
  if (node->getType() == Node::NK_Alias)
    return Fail(path, context, "aliases are not allowed", error);
  return true;
}

bool IsQuotedScalar(StringRef raw) {
  raw = raw.trim();
  return raw.size() >= 2 &&
         ((raw.front() == '\'' && raw.back() == '\'') ||
          (raw.front() == '"' && raw.back() == '"'));
}

bool PlainScalarIsNonString(const string& value) {
  const StringRef scalar(value);
  return llvm::yaml::isNull(scalar) || llvm::yaml::isBool(scalar) ||
         llvm::yaml::isNumeric(scalar);
}

bool StringScalar(Node* node, const string& path, const string& context,
                  const string& field, bool nonempty, string* value,
                  string* error) {
  if (!RejectGraphFeatures(node, path, context, error))
    return false;
  ScalarNode* scalar = llvm::dyn_cast<ScalarNode>(node);
  if (!scalar)
    return Fail(path, context, "'" + field + "' must be a scalar", error);
  llvm::SmallString<128> storage;
  StringRef decoded = scalar->getValue(storage);
  if (decoded.find('\0') != StringRef::npos)
    return Fail(path, context, "'" + field + "' contains an embedded NUL", error);
  *value = decoded.str();
  if (!IsQuotedScalar(scalar->getRawValue()) && PlainScalarIsNonString(*value)) {
    return Fail(path, context, "'" + field + "' must be a string scalar", error);
  }
  if (nonempty && value->empty()) {
    return Fail(path, context, "'" + field +
                "' must be a non-empty scalar", error);
  }
  return true;
}

bool VersionScalar(Node* node, const string& path, string* value,
                   string* error) {
  if (!RejectGraphFeatures(node, path, "", error))
    return false;
  ScalarNode* scalar = llvm::dyn_cast<ScalarNode>(node);
  if (!scalar)
    return Fail(path, "", "'schema_version' must be an integer", error);
  llvm::SmallString<32> storage;
  StringRef decoded = scalar->getValue(storage);
  StringRef raw = scalar->getRawValue().trim();
  if (IsQuotedScalar(raw) || decoded.empty() ||
      !std::all_of(decoded.begin(), decoded.end(),
                   [](unsigned char c) { return std::isdigit(c); })) {
    return Fail(path, "", "'schema_version' must be an integer", error);
  }
  *value = decoded.str();
  return true;
}

bool Mapping(Node* node, const string& path, const string& context,
             const string& field, MappingNode** result, string* error) {
  if (!RejectGraphFeatures(node, path, context, error))
    return false;
  *result = llvm::dyn_cast<MappingNode>(node);
  if (!*result)
    return Fail(path, context, "'" + field + "' must be a mapping", error);
  return true;
}

bool MappingKey(KeyValueNode& item, const string& path, const string& context,
                string* key, string* error) {
  Node* key_node = item.getKey();
  if (!RejectGraphFeatures(key_node, path, context, error))
    return false;
  ScalarNode* scalar = llvm::dyn_cast<ScalarNode>(key_node);
  if (!scalar)
    return Fail(path, context, "mapping keys must be scalars", error);
  llvm::SmallString<64> storage;
  StringRef decoded = scalar->getValue(storage);
  if (decoded.empty())
    return Fail(path, context, "mapping keys must be non-empty", error);
  if (decoded.find('\0') != StringRef::npos)
    return Fail(path, context, "mapping key contains an embedded NUL", error);
  *key = decoded.str();
  return true;
}

bool ParsePattern(Node* node, const string& path, const string& context,
                  const string& field, RawPattern* result, string* error) {
  MappingNode* mapping = nullptr;
  if (!Mapping(node, path, context, field, &mapping, error))
    return false;

  std::set<string> seen;
  std::optional<RawPattern> pattern;
  for (KeyValueNode& item : *mapping) {
    string key;
    if (!MappingKey(item, path, context, &key, error))
      return false;
    if (!seen.insert(key).second) {
      return Fail(path, context, "duplicate pattern key '" + key + "'", error);
    }
    if (key != "glob" && key != "regex") {
      return Fail(path, context, "unknown pattern key '" + key + "'", error);
    }
    string value;
    if (!StringScalar(item.getValue(), path, context, key, true, &value, error))
      return false;
    pattern = RawPattern{key == "glob" ? RawPattern::Glob : RawPattern::Regex,
                         std::move(value)};
  }
  if (seen.size() != 1) {
    return Fail(path, context,
                "'" + field +
                    "' must contain exactly one of 'glob' or 'regex'",
                error);
  }
  *result = std::move(*pattern);
  return true;
}

bool ParseMatch(Node* node, const string& path, const string& context,
                RawMatch* result, string* error) {
  MappingNode* mapping = nullptr;
  if (!Mapping(node, path, context, "match", &mapping, error))
    return false;

  std::set<string> seen;
  for (KeyValueNode& item : *mapping) {
    string key;
    if (!MappingKey(item, path, context, &key, error))
      return false;
    if (!seen.insert(key).second)
      return Fail(path, context, "duplicate match key '" + key + "'", error);

    Node* value = item.getValue();
    if (key == "includer" || key == "includee") {
      RawPattern pattern;
      if (!ParsePattern(value, path, context, key, &pattern, error))
        return false;
      if (key == "includer")
        result->includer = std::move(pattern);
      else
        result->includee = std::move(pattern);
    } else if (key == "file_kind" || key == "active_condition" ||
               key == "symbol" || key == "configuration") {
      string scalar;
      if (!StringScalar(value, path, context, key, true, &scalar, error))
        return false;
      if (key == "file_kind")
        result->file_kind = std::move(scalar);
      else if (key == "active_condition")
        result->active_condition = std::move(scalar);
      else if (key == "symbol")
        result->symbol = std::move(scalar);
      else
        result->configuration = std::move(scalar);
    } else {
      return Fail(path, context, "unknown match key '" + key + "'", error);
    }
  }
  if (seen.empty())
    return Fail(path, context, "match must contain at least one dimension", error);
  return true;
}

bool ParseRule(Node* node, size_t index, const string& path, RawRule* result,
               string* error) {
  const string context = "rule " + std::to_string(index);
  if (!RejectGraphFeatures(node, path, context, error))
    return false;
  MappingNode* mapping = llvm::dyn_cast<MappingNode>(node);
  if (!mapping)
    return Fail(path, context, context + " must be a mapping", error);

  std::set<string> seen;
  for (KeyValueNode& item : *mapping) {
    string key;
    if (!MappingKey(item, path, context, &key, error))
      return false;
    if (!seen.insert(key).second)
      return Fail(path, context, "duplicate rule key '" + key + "'", error);
    if (key == "id") {
      if (!StringScalar(item.getValue(), path, context, key, true,
                  &result->id, error))
        return false;
    } else if (key == "action") {
      if (!StringScalar(item.getValue(), path, context, key, true,
                  &result->action, error))
        return false;
    } else if (key == "match") {
      if (!ParseMatch(item.getValue(), path, context, &result->match, error))
        return false;
    } else {
      return Fail(path, context, "unknown rule key '" + key + "'", error);
    }
  }
  for (const char* required : {"id", "action", "match"}) {
    if (!seen.count(required)) {
      return Fail(path, context, "missing required rule key '" +
                  string(required) + "'", error);
    }
  }
  return true;
}

bool ParsePolicy(Node* root, const string& path, RawPolicy* result,
                 string* error) {
  if (!RejectGraphFeatures(root, path, "", error))
    return false;
  MappingNode* mapping = llvm::dyn_cast<MappingNode>(root);
  if (!mapping)
    return Fail(path, "", "top level must be a mapping", error);

  std::set<string> seen;
  string schema_version;
  for (KeyValueNode& item : *mapping) {
    string key;
    if (!MappingKey(item, path, "", &key, error))
      return false;
    if (!seen.insert(key).second) {
      return Fail(path, "", "duplicate top-level key '" + key + "'", error);
    }
    if (key == "schema_version") {
      if (!VersionScalar(item.getValue(), path, &schema_version, error))
        return false;
    } else if (key == "rules") {
      Node* rules_node = item.getValue();
      if (!RejectGraphFeatures(rules_node, path, "", error))
        return false;
      SequenceNode* rules = llvm::dyn_cast<SequenceNode>(rules_node);
      if (!rules)
        return Fail(path, "", "'rules' must be a sequence", error);
      size_t index = 0;
      for (Node& rule_node : *rules) {
        RawRule rule;
        if (!ParseRule(&rule_node, ++index, path, &rule, error))
          return false;
        result->rules.push_back(std::move(rule));
      }
    } else {
      return Fail(path, "", "unknown top-level key '" + key + "'", error);
    }
  }
  for (const char* required : {"schema_version", "rules"}) {
    if (!seen.count(required)) {
      return Fail(path, "", "missing required top-level key '" +
                  string(required) + "'", error);
    }
  }
  if (schema_version != "1") {
    return Fail(path, "", "unsupported schema_version '" + schema_version +
                "'", error);
  }
  return true;
}

bool IsAsciiAlpha(unsigned char c) {
  return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
}

bool IsAsciiAlnum(unsigned char c) {
  return IsAsciiAlpha(c) || (c >= '0' && c <= '9');
}

bool ValidRuleId(const string& id) {
  if (id.empty() || !IsAsciiAlpha(static_cast<unsigned char>(id[0])))
    return false;
  return std::all_of(id.begin() + 1, id.end(), [](unsigned char c) {
    return IsAsciiAlnum(c) || c == '_' || c == '.' || c == '-';
  });
}

bool ParseAction(const string& value, ProjectPolicyAction* action) {
  static const std::map<string, ProjectPolicyAction> actions = {
      {"deny_add", ProjectPolicyAction::DenyAdd},
      {"deny_remove", ProjectPolicyAction::DenyRemove},
      {"keep", ProjectPolicyAction::Keep},
      {"associate", ProjectPolicyAction::Associate},
      {"export", ProjectPolicyAction::Export},
      {"prefer_provider", ProjectPolicyAction::PreferProvider},
      {"report_only", ProjectPolicyAction::ReportOnly},
  };
  const auto found = actions.find(value);
  if (found == actions.end())
    return false;
  *action = found->second;
  return true;
}

string ActionName(ProjectPolicyAction action) {
  switch (action) {
    case ProjectPolicyAction::DenyAdd: return "deny_add";
    case ProjectPolicyAction::DenyRemove: return "deny_remove";
    case ProjectPolicyAction::Keep: return "keep";
    case ProjectPolicyAction::Associate: return "associate";
    case ProjectPolicyAction::Export: return "export";
    case ProjectPolicyAction::PreferProvider: return "prefer_provider";
    case ProjectPolicyAction::ReportOnly: return "report_only";
  }
  return "unknown";
}

bool ActionInFamily(ProjectPolicyAction action,
                    ProjectPolicyActionFamily family) {
  if (family == ProjectPolicyActionFamily::Addition)
    return action == ProjectPolicyAction::DenyAdd;
  return action == ProjectPolicyAction::DenyRemove ||
         action == ProjectPolicyAction::Keep;
}


bool ValidFileKind(const string& kind) {
  return kind == "source" || kind == "header" ||
         kind == "inline_header" || kind == "other";
}

bool HasTrailingUnescapedBackslash(const string& pattern) {
  size_t count = 0;
  for (size_t i = pattern.size(); i > 0 && pattern[i - 1] == '\\'; --i)
    ++count;
  return count % 2 != 0;
}

class PatternMatcher {
 public:
  static std::optional<PatternMatcher> Compile(
      const RawPattern& raw, const string& path, const string& context,
      const string& field, string* error) {
    PatternMatcher result;
    result.kind_ = raw.kind;
    result.pattern_ = std::make_unique<string>(raw.value);
    if (raw.kind == RawPattern::Glob) {
      if (HasTrailingUnescapedBackslash(raw.value)) {
        Fail(path, context, "invalid glob in '" + field +
             "': trailing unescaped backslash", error);
        return std::nullopt;
      }
      llvm::Expected<llvm::GlobPattern> compiled =
          llvm::GlobPattern::create(*result.pattern_);
      if (!compiled) {
        Fail(path, context, "invalid glob in '" + field + "': " +
             llvm::toString(compiled.takeError()), error);
        return std::nullopt;
      }
      result.glob_ =
          std::make_unique<llvm::GlobPattern>(std::move(*compiled));
    } else {
      result.regex_ = std::make_unique<llvm::Regex>(*result.pattern_);
      string regex_error;
      if (!result.regex_->isValid(regex_error)) {
        Fail(path, context, "invalid regex in '" + field + "': " +
             regex_error, error);
        return std::nullopt;
      }
    }
    return result;
  }

  PatternMatcher(PatternMatcher&&) = default;
  PatternMatcher& operator=(PatternMatcher&&) = default;

  bool Matches(const string& value) const {
    if (kind_ == RawPattern::Glob)
      return glob_->match(value);
    llvm::SmallVector<StringRef, 4> matches;
    if (!regex_->match(value, &matches) || matches.empty())
      return false;
    return matches[0] == value;
  }

 private:
  PatternMatcher() = default;

  RawPattern::Kind kind_ = RawPattern::Glob;
  std::unique_ptr<string> pattern_;
  std::unique_ptr<llvm::GlobPattern> glob_;
  std::unique_ptr<llvm::Regex> regex_;
};

}  // namespace

class ProjectPolicy::Rule {
 public:
  string id;
  ProjectPolicyAction action = ProjectPolicyAction::DenyAdd;
  std::optional<PatternMatcher> includer;
  std::optional<PatternMatcher> includee;
  std::optional<string> file_kind;
  std::optional<string> active_condition;
  std::optional<string> symbol;
  std::optional<string> configuration;

  bool Matches(const ProjectPolicyMatchContext& context) const {
    if (includer && !includer->Matches(context.includer))
      return false;
    if (includee && !includee->Matches(context.includee))
      return false;
    if (file_kind && *file_kind != context.file_kind)
      return false;
    if (active_condition && *active_condition != context.active_condition)
      return false;
    if (configuration && *configuration != context.configuration)
      return false;
    if (symbol && std::find(context.symbols.begin(), context.symbols.end(),
                            *symbol) == context.symbols.end()) {
      return false;
    }
    return true;
  }
};

ProjectPolicy::~ProjectPolicy() = default;

std::unique_ptr<ProjectPolicy> ProjectPolicy::Load(const string& path,
                                                   string* error) {
  llvm::SmallString<256> real_path;
  if (std::error_code ec = llvm::sys::fs::real_path(path, real_path)) {
    *error = "cannot open project policy '" + path + "': " + ec.message();
    return nullptr;
  }
  const string canonical_path = NormalizeFilePath(real_path.str().str());

  llvm::ErrorOr<std::unique_ptr<MemoryBuffer>> buffer =
      MemoryBuffer::getFile(canonical_path);
  if (std::error_code ec = buffer.getError()) {
    *error = "cannot open project policy '" + canonical_path + "': " +
             ec.message();
    return nullptr;
  }
  StringRef bytes = buffer.get()->getBuffer();
  if (bytes.find('\0') != StringRef::npos) {
    *error = Context(canonical_path, "", "embedded NUL bytes are not allowed");
    return nullptr;
  }

  llvm::SourceMgr source_manager;
  std::error_code parse_error;
  llvm::yaml::Stream stream(buffer.get()->getMemBufferRef(), source_manager,
                            false, &parse_error);
  auto document = stream.begin();
  if (document == stream.end()) {
    *error = Context(canonical_path, "", "exactly one YAML document is required");
    return nullptr;
  }
  Node* root = document->getRoot();
  if (!root || stream.failed() || parse_error) {
    *error = Context(canonical_path, "", "malformed YAML/JSON");
    return nullptr;
  }

  RawPolicy raw;
  if (!ParsePolicy(root, canonical_path, &raw, error))
    return nullptr;
  ++document;
  if (document != stream.end()) {
    *error = Context(canonical_path, "", "exactly one YAML document is allowed");
    return nullptr;
  }
  if (stream.failed() || parse_error) {
    *error = Context(canonical_path, "", "malformed YAML/JSON");
    return nullptr;
  }

  auto result = std::unique_ptr<ProjectPolicy>(new ProjectPolicy);
  result->path_ = canonical_path;
  std::set<string> ids;
  for (size_t index = 0; index < raw.rules.size(); ++index) {
    RawRule& raw_rule = raw.rules[index];
    const string context = "rule " + std::to_string(index + 1);
    if (!ValidRuleId(raw_rule.id)) {
      Fail(canonical_path, context, "invalid rule id '" + raw_rule.id + "'",
           error);
      return nullptr;
    }
    if (!ids.insert(raw_rule.id).second) {
      Fail(canonical_path, context, "duplicate rule id '" + raw_rule.id + "'",
           error);
      return nullptr;
    }

    auto rule = std::make_unique<Rule>();
    rule->id = std::move(raw_rule.id);
    if (!ParseAction(raw_rule.action, &rule->action)) {
      Fail(canonical_path, context, "unknown action '" + raw_rule.action + "'",
           error);
      return nullptr;
    }
    if (raw_rule.match.file_kind &&
        !ValidFileKind(*raw_rule.match.file_kind)) {
      Fail(canonical_path, context,
           "unknown file_kind '" + *raw_rule.match.file_kind + "'", error);
      return nullptr;
    }
    if (raw_rule.match.includer) {
      auto matcher = PatternMatcher::Compile(*raw_rule.match.includer,
          canonical_path, context, "includer", error);
      if (!matcher)
        return nullptr;
      rule->includer = std::move(*matcher);
    }
    if (raw_rule.match.includee) {
      auto matcher = PatternMatcher::Compile(*raw_rule.match.includee,
          canonical_path, context, "includee", error);
      if (!matcher)
        return nullptr;
      rule->includee = std::move(*matcher);
    }
    rule->file_kind = std::move(raw_rule.match.file_kind);
    rule->active_condition = std::move(raw_rule.match.active_condition);
    rule->symbol = std::move(raw_rule.match.symbol);
    rule->configuration = std::move(raw_rule.match.configuration);
    result->rules_.push_back(std::move(rule));
  }
  return result;
}

bool ProjectPolicy::ValidateRuntimeCapabilities(bool has_configuration,
                                                string* error) const {
  for (size_t index = 0; index < rules_.size(); ++index) {
    const Rule& rule = *rules_[index];
    const string context = "rule " + std::to_string(index + 1) +
                           " ('" + rule.id + "')";
    if (rule.action != ProjectPolicyAction::DenyAdd &&
        rule.action != ProjectPolicyAction::DenyRemove &&
        rule.action != ProjectPolicyAction::Keep) {
      return Fail(path_, context, "action '" + ActionName(rule.action) +
                  "' is recognized but not enabled in this phase", error);
    }
    if (rule.active_condition) {
      return Fail(path_, context,
                  "active_condition is recognized but unavailable in this phase",
                  error);
    }
    if (rule.configuration && !has_configuration) {
      return Fail(path_, context,
                  "configuration matching requires --configuration", error);
    }
  }
  return true;
}

std::optional<ProjectPolicyDecision> ProjectPolicy::FirstMatchingRule(
    ProjectPolicyAction action,
    const ProjectPolicyMatchContext& context) const {
  for (const std::unique_ptr<Rule>& rule : rules_) {
    if (rule->action == action && rule->Matches(context))
      return ProjectPolicyDecision{action, path_, rule->id};
  }
  return std::nullopt;
}

std::optional<ProjectPolicyDecision> ProjectPolicy::FirstMatchingRule(
    ProjectPolicyActionFamily family,
    const ProjectPolicyMatchContext& context) const {
  for (const std::unique_ptr<Rule>& rule : rules_) {
    if (ActionInFamily(rule->action, family) && rule->Matches(context))
      return ProjectPolicyDecision{rule->action, path_, rule->id};
  }
  return std::nullopt;
}

string ProjectPolicyFileKind(const string& path) {
  if (IsInlineHeaderFile(path))
    return "inline_header";

  string lower = path;
  std::transform(lower.begin(), lower.end(), lower.begin(),
                 [](unsigned char c) { return std::tolower(c); });
  static const char* const header_extensions[] = {
      ".h", ".hh", ".hpp", ".hxx"};
  static const char* const source_extensions[] = {
      ".c", ".cc", ".cxx", ".cpp", ".c++", ".cp", ".m", ".mm"};
  for (const char* extension : header_extensions) {
    if (EndsWith(lower, extension))
      return "header";
  }
  for (const char* extension : source_extensions) {
    if (EndsWith(lower, extension))
      return "source";
  }
  return "other";
}

}  // namespace include_what_you_use
