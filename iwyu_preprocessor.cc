//===--- iwyu_preprocessor.cc - handle #includes/#defines for iwyu --------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#include "iwyu_preprocessor.h"

#include <algorithm>
#include <cctype>
#include <cstddef>                      // for size_t
#include <cstring>
#include <iterator>
#include <map>
#include <string>                       // for string, basic_string, etc
#include <utility>                      // for pair, make_pair
#include <vector>

#include "iwyu_ast_util.h"
#include "iwyu_globals.h"
#include "iwyu_include_picker.h"
#include "iwyu_lexer_utils.h"
#include "iwyu_location_util.h"
#include "iwyu_output.h"
#include "iwyu_path_util.h"
#include "iwyu_port.h"  // for CHECK_
#include "iwyu_stl_util.h"
#include "iwyu_string_util.h"
#include "iwyu_verrs.h"
#include "llvm/Support/raw_ostream.h"
#include "clang/AST/ASTContext.h"
#include "clang/AST/Decl.h"
#include "clang/AST/DeclCXX.h"
#include "clang/AST/DeclTemplate.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/AST/Stmt.h"
#include "clang/AST/TypeLoc.h"
#include "clang/Basic/IdentifierTable.h"
#include "clang/Lex/HeaderSearch.h"
#include "clang/Lex/Lexer.h"
#include "clang/Lex/MacroInfo.h"

using clang::FileEntry;
using clang::FileEntryRef;
using clang::FileID;
using clang::MacroDefinition;
using clang::MacroDirective;
using clang::MacroInfo;
using clang::NamedDecl;
using clang::Preprocessor;
using clang::SourceLocation;
using clang::SourceRange;
using clang::Token;
using llvm::errs;
using llvm::StringRef;
using std::make_pair;
using std::string;
using std::to_string;

namespace SrcMgr = clang::SrcMgr;

namespace include_what_you_use {

namespace {
// TODO(dsturtevant): Perhaps make this method accessible iwyu-wide.
// At first blush, iwyu_output is the place to put it, but that would
// introduce a circular dependency between iwyu_output and
// iwyu_ast_util.
void Warn(IwyuPreprocessorInfo* preprocessor_info, SourceLocation loc,
          const string& message) {
  if (GlobalFlags().output_format == CommandlineFlags::kJsonOutput) {
    const FileEntry* origin_file = GetFileEntry(loc);
    IwyuFileInfo* file_info = nullptr;
    if (origin_file && ShouldReportIWYUViolationsFor(origin_file))
      file_info = preprocessor_info->FileInfoFor(origin_file);
    if (!file_info && preprocessor_info->main_file()) {
      file_info = preprocessor_info->FileInfoFor(
          preprocessor_info->main_file());
    }
    if (file_info) {
      file_info->AddStructuredDiagnostic(
          loc, "preprocessor_warning", message);
    }
    return;
  }
  errs() << PrintableLoc(loc) << ": warning: " << message << "\n";
}

// For use with no_forward_declare. Allow people to specify forward
// declares with or without the leading "::", and don't make them use
// (anonymous namespace).
string NormalizeNamespaces(string symbol) {
  if (StartsWith(symbol, "::")) {
    symbol = symbol.substr(2);
  }
  const char kAnonymousNamespaceQualifier[] = "(anonymous namespace)::";
  for (;;) {
    const string::size_type index = symbol.find(kAnonymousNamespaceQualifier);
    if (index == string::npos) {
      break;
    }
    symbol = (symbol.substr(0, index) +
              symbol.substr(index + strlen(kAnonymousNamespaceQualifier)));
  }
  return symbol;
}

bool StripHeaderExtension(string* path) {
  static const char* const extensions[] = {".h", ".hh", ".hpp", ".hxx"};
  string lower = *path;
  std::transform(lower.begin(), lower.end(), lower.begin(),
                 [](unsigned char c) { return std::tolower(c); });
  for (const char* extension : extensions) {
    if (EndsWith(lower, extension)) {
      path->resize(path->size() - strlen(extension));
      return true;
    }
  }
  return false;
}

// Dotted inline headers have export semantics not shared by legacy -inl
// headers, but generic classification belongs in IsInlineHeaderFile.
bool IsDottedInlineHeader(string path) {
  if (!IsInlineHeaderFile(path))
    return false;
  path = NormalizeFilePath(path);
  if (!StripHeaderExtension(&path))
    return false;
  std::transform(path.begin(), path.end(), path.begin(),
                 [](unsigned char c) { return std::tolower(c); });
  return EndsWith(path, ".inline");
}

bool IsExactPlainCounterpart(string inline_path, string plain_path) {
  inline_path = NormalizeFilePath(inline_path);
  plain_path = NormalizeFilePath(plain_path);
  if (IsInlineHeaderFile(plain_path) ||
      !StripHeaderExtension(&inline_path) ||
      !StripHeaderExtension(&plain_path)) {
    return false;
  }
  string lower_inline = inline_path;
  std::transform(lower_inline.begin(), lower_inline.end(), lower_inline.begin(),
                 [](unsigned char c) { return std::tolower(c); });
  if (!EndsWith(lower_inline, ".inline"))
    return false;
  inline_path.resize(inline_path.size() - strlen(".inline"));
  return inline_path == plain_path;
}

struct RawConditionalDirective {
  unsigned hash_offset;
  unsigned directive_offset;
  string kind;
};

bool IsHorizontalSpace(char ch) {
  return ch == ' ' || ch == '\t' || ch == '\v' || ch == '\f' || ch == '\r';
}

bool IsIdentifierByte(char ch) {
  const unsigned char value = static_cast<unsigned char>(ch);
  return std::isalnum(value) || ch == '_';
}

size_t RawStringEnd(StringRef source, size_t offset) {
  static const char* const prefixes[] = {"R\"", "u8R\"", "uR\"",
                                         "UR\"", "LR\""};
  for (const char* prefix : prefixes) {
    const size_t prefix_length = strlen(prefix);
    if (source.substr(offset).take_front(prefix_length) != prefix)
      continue;
    const size_t delimiter_begin = offset + prefix_length;
    size_t open = delimiter_begin;
    while (open < source.size() && source[open] != '(') {
      const char ch = source[open];
      if (open - delimiter_begin >= 16 || IsHorizontalSpace(ch) ||
          ch == '\n' || ch == ' ' || ch == '(' || ch == ')' || ch == '\\') {
        return string::npos;
      }
      ++open;
    }
    if (open == source.size())
      return string::npos;
    const StringRef delimiter = source.slice(delimiter_begin, open);
    const string closing = ")" + delimiter.str() + "\"";
    const size_t close = source.find(closing, open + 1);
    return close == StringRef::npos ? string::npos : close + closing.size();
  }
  return string::npos;
}

vector<RawConditionalDirective> ScanConditionalDirectives(StringRef source) {
  string logical;
  vector<unsigned> physical;
  logical.reserve(source.size());
  physical.reserve(source.size());
  for (size_t offset = 0; offset < source.size();) {
    if (source[offset] == '\\' && offset + 1 < source.size() &&
        source[offset + 1] == '\n') {
      offset += 2;
      continue;
    }
    if (source[offset] == '\\' && offset + 2 < source.size() &&
        source[offset + 1] == '\r' && source[offset + 2] == '\n') {
      offset += 3;
      continue;
    }
    logical.push_back(source[offset]);
    physical.push_back(static_cast<unsigned>(offset));
    ++offset;
  }

  vector<RawConditionalDirective> directives;
  bool at_line_start = true;
  bool in_block_comment = false;
  for (size_t index = 0; index < logical.size();) {
    if (in_block_comment) {
      if (logical.compare(index, 2, "*/") == 0) {
        in_block_comment = false;
        index += 2;
      } else {
        if (logical[index] == '\n')
          at_line_start = true;
        ++index;
      }
      continue;
    }
    if (logical.compare(index, 2, "//") == 0) {
      const size_t newline = logical.find('\n', index + 2);
      if (newline == string::npos)
        break;
      index = newline;
      continue;
    }
    if (logical.compare(index, 2, "/*") == 0) {
      in_block_comment = true;
      index += 2;
      continue;
    }
    if (logical[index] == '\n') {
      at_line_start = true;
      ++index;
      continue;
    }
    if (at_line_start && IsHorizontalSpace(logical[index])) {
      ++index;
      continue;
    }
    if (at_line_start && logical[index] == '#') {
      size_t keyword = index + 1;
      for (;;) {
        while (keyword < logical.size() &&
               IsHorizontalSpace(logical[keyword])) {
          ++keyword;
        }
        if (logical.compare(keyword, 2, "/*") != 0)
          break;
        const size_t end = logical.find("*/", keyword + 2);
        const size_t newline = logical.find('\n', keyword);
        if (end == string::npos ||
            (newline != string::npos && newline < end)) {
          keyword = string::npos;
          break;
        }
        keyword = end + 2;
      }
      if (keyword != string::npos && keyword < logical.size() &&
          logical.compare(keyword, 2, "//") != 0 &&
          logical[keyword] != '\n') {
        size_t end = keyword;
        while (end < logical.size() && IsIdentifierByte(logical[end]))
          ++end;
        const string kind = logical.substr(keyword, end - keyword);
        if (kind == "if" || kind == "ifdef" || kind == "ifndef" ||
            kind == "elif" || kind == "elifdef" || kind == "elifndef" ||
            kind == "else" || kind == "endif") {
          directives.push_back(
              {physical[index], physical[keyword], kind});
        }
      }
      at_line_start = false;
      ++index;
      continue;
    }

    const size_t raw_end = RawStringEnd(logical, index);
    if (raw_end != string::npos) {
      at_line_start = false;
      index = raw_end;
      continue;
    }
    if (logical[index] == '\'' || logical[index] == '"') {
      const char quote = logical[index++];
      bool escaped = false;
      at_line_start = false;
      while (index < logical.size()) {
        const char ch = logical[index++];
        if (escaped) {
          escaped = false;
        } else if (ch == '\\') {
          escaped = true;
        } else if (ch == quote) {
          break;
        } else if (ch == '\n') {
          at_line_start = true;
          break;
        }
      }
      continue;
    }
    at_line_start = false;
    ++index;
  }
  return directives;
}

}  // namespace

//------------------------------------------------------------
// Utilities for examining source files.

// For a particular #include line that include_loc points to,
// returns the include as written by the user, including <> or "".
// This works even for computed #includes ('#include MACRO'): we
// point to the string the macro expands to.
// Simplifying wrapper around the iwyu_lexer function.
static string GetIncludeNameAsWritten(SourceLocation include_loc) {
  return GetIncludeNameAsWritten(include_loc, DefaultDataGetter());
}

//------------------------------------------------------------
// Utilities on macros.

static string GetName(const Token& token) {
  return token.getIdentifierInfo()->getName().str();
}

// Returns the exact physical source line containing an include token. The
// canonical spelling used for desired state is retained separately.
static string GetSourceLineAsWritten(SourceLocation include_loc) {
  const SourceLocation spelling_loc = GetInstantiationLoc(include_loc);
  if (spelling_loc.isInvalid())
    return string();
  const clang::SourceManager& source_manager = *GlobalSourceManager();
  const clang::FileID file_id = source_manager.getFileID(spelling_loc);
  const unsigned line = source_manager.getSpellingLineNumber(spelling_loc);
  const SourceLocation line_begin = source_manager.translateLineCol(
      file_id, line, 1);
  if (line_begin.isInvalid())
    return string();
  return GetSourceTextUntilEndOfLine(line_begin, DefaultDataGetter()).str();
}

//------------------------------------------------------------
// Utilities for handling iwyu-specific pragma comments.

namespace {

// Given a vector of tokens, a token to match, and an expected number
// of tokens, return true if the number of tokens is at least the
// expected number and the first token matches the given token, else
// false. In addition, if in the 'return true' case there are more
// tokens than expected, warn if the first one doesn't start "//" or
// "*/", the latter presumably closing a C-style comment.
// <loc>, which is only used for a warning message, should refer
// to the beginning of the comment containing the tokens.
bool MatchOneToken(const vector<string>& tokens,
                   const string& token,
                   size_t num_expected_tokens,
                   SourceLocation loc) {
  if (tokens.size() < num_expected_tokens) {
    return false;
  }
  if (tokens[0] != token) {
    return false;
  }
  if (tokens.size() > num_expected_tokens &&
      !StartsWith(tokens[num_expected_tokens], "//") &&
      !StartsWith(tokens[num_expected_tokens], "*/")) {
    VERRS(4) << PrintableLoc(loc) << ": warning: Extra tokens on pragma line\n";
  }
  return true;
}

// Given a vector of tokens, two tokens to match, and an expected
// number of tokens, return true if the number of tokens is at least
// the expected number and the first two tokens match the given
// tokens, else false. In addition, if in the 'return true' case there
// are more tokens than expected, warn if the first one doesn't start
// "//".
// <loc>, which is only used for a warning message, should refer
// to the beginning of the comment containing the tokens.
bool MatchTwoTokens(const vector<string>& tokens,
                    const string& token1,
                    const string& token2,
                    size_t num_expected_tokens,
                    SourceLocation loc) {
  if (tokens.size() < num_expected_tokens) {
    return false;
  }
  if (tokens[0] != token1) {
    return false;
  }
  if (tokens[1] != token2) {
    return false;
  }
  if (tokens.size() > num_expected_tokens &&
      !StartsWith(tokens[num_expected_tokens], "//") &&
      !StartsWith(tokens[num_expected_tokens], "*/")) {
    // Accept but warn.
    VERRS(4) << PrintableLoc(loc) << ": warning: Extra tokens on pragma line\n";
  }
  return true;
}

}  // namespace

// Call this function only when the given file is the one currently
// being processed (or a file directly including it, when the current
// file has not processed any comments yet). Return true if only if
// there is an open begin_exports pragma in the current state of the
// parse of the given file. Note that there may be open begin_exports
// in including files. They don't matter for this function.
bool IwyuPreprocessorInfo::HasOpenBeginExports(const FileEntry* file) const {
  return !begin_exports_location_stack_.empty() &&
      GetFileEntry(begin_exports_location_stack_.top()) == file;
}

// Only call this when the given files is the one being processed
// Only return true if there is an open begin_keep pragma in the current
// state of the parse of the given file.
bool IwyuPreprocessorInfo::HasOpenBeginKeep(const FileEntry* file) const {
  return !begin_keep_location_stack_.empty() &&
      GetFileEntry(begin_keep_location_stack_.top()) == file;
}

bool IwyuPreprocessorInfo::HandleComment(Preprocessor& pp,
                                         SourceRange comment_range) {
  HandlePragmaComment(comment_range);
  return false;  // No tokens pushed.
}

void IwyuPreprocessorInfo::HandlePragmaComment(SourceRange comment_range) {
  const SourceLocation begin_loc = comment_range.getBegin();
  const SourceLocation end_loc = comment_range.getEnd();
  const char* begin_text = DefaultDataGetter().GetCharacterData(begin_loc);
  const char* end_text = DefaultDataGetter().GetCharacterData(end_loc);
  string pragma_text(begin_text, end_text);
  const FileEntry* const this_file_entry = GetFileEntry(begin_loc);

  // Pragmas must start comments.
  if (!StripLeft(&pragma_text, "// IWYU pragma: ") &&
      !StripLeft(&pragma_text, "/* IWYU pragma: ")) {
    return;
  }
  const vector<string> tokens =
      SplitOnWhiteSpacePreservingQuotes(pragma_text, 0);
  if (HasOpenBeginExports(this_file_entry)) {
    if (MatchOneToken(tokens, "end_exports", 1, begin_loc)) {
      ERRSYM(this_file_entry) << "end_exports pragma seen\n";
      SourceLocation export_loc_begin = begin_exports_location_stack_.top();
      begin_exports_location_stack_.pop();
      SourceRange export_range(export_loc_begin, begin_loc);
      export_location_ranges_.insert(
          std::make_pair(this_file_entry, export_range));
    } else {
      // No pragma allowed within "begin_exports" - "end_exports"
      Warn(this, begin_loc, "Expected end_exports pragma");
    }
    return;
  }

  if (HasOpenBeginKeep(this_file_entry)) {
    if (MatchOneToken(tokens, "end_keep", 1, begin_loc)) {
      ERRSYM(this_file_entry) << "end_keep pragma seen\n";
      SourceLocation keep_loc_begin = begin_keep_location_stack_.top();
      begin_keep_location_stack_.pop();
      SourceRange keep_range(keep_loc_begin, begin_loc);
      keep_location_ranges_.insert(std::make_pair(this_file_entry, keep_range));
    } else {
      // No pragmas allowed within "begin_keep" - "end_keep"
      Warn(this, begin_loc, "Expected end_keep pragma");
    }
    return;
  }

  if (MatchOneToken(tokens, "begin_exports", 1, begin_loc)) {
    ERRSYM(this_file_entry) << "begin_exports pragma seen\n";
    begin_exports_location_stack_.push(begin_loc);
    return;
  }

  if (MatchOneToken(tokens, "end_exports", 1, begin_loc)) {
    Warn(this, begin_loc, "end_exports without a begin_exports");
    return;
  }

  if (MatchOneToken(tokens, "begin_keep", 1, begin_loc)) {
    ERRSYM(this_file_entry) << "begin_keep pragma seen\n";
    begin_keep_location_stack_.push(begin_loc);
    return;
  }

  if (MatchOneToken(tokens, "end_keep", 1, begin_loc)) {
    Warn(this, begin_loc, "end_keep without a begin_keep");
    return;
  }

  if (MatchTwoTokens(tokens, "private,", "include", 3, begin_loc)) {
    // 3rd token should be a quoted header.
    const string& suggested = tokens[2];
    if (!IsQuotedInclude(suggested)) {
      Warn(this, begin_loc, "Suggested include must be a quoted header");
      return;
    }

    const string quoted_this_file =
        ConvertToQuotedInclude(GetFilePath(begin_loc));

    VERRS(8) << "Adding dynamic mapping for private pragma\n";
    MutableGlobalIncludePicker()->AddMapping(quoted_this_file,
                                             MappedInclude(suggested));
    MutableGlobalIncludePicker()->MarkIncludeAsPrivate(quoted_this_file);
    return;
  }

  if (MatchOneToken(tokens, "private", 1, begin_loc)) {
    const string path_this_file = GetFilePath(begin_loc);
    MutableGlobalIncludePicker()->MarkPathAsPrivate(path_this_file);
    ERRSYM(this_file_entry) << "Adding private path: "
                            << path_this_file << "\n";
    return;
  }

  if (MatchOneToken(tokens, "no_include", 2, begin_loc)) {
    // 2nd token should be an quoted header.
    const string& inhibited = tokens[1];
    if (!IsQuotedInclude(inhibited)) {
      Warn(this, begin_loc, "Inhibited include must be a quoted header");
      return;
    }

    no_include_map_[this_file_entry].insert(inhibited);
    ERRSYM(this_file_entry) << "Inhibiting include of "
                            << inhibited << "\n";
    return;
  }

  if (MatchOneToken(tokens, "no_forward_declare", 2, begin_loc)) {
    // 2nd token should be the qualified name of a symbol.
    const string normalized_symbol = NormalizeNamespaces(tokens[1]);
    no_forward_declare_map_[this_file_entry].insert(normalized_symbol);
    ERRSYM(this_file_entry) << "Inhibiting forward-declare of "
                            << normalized_symbol << "\n";
    return;
  }

  if (MatchOneToken(tokens, "friend", 2, begin_loc)) {
    // 2nd token should be a regex.
    string regex = tokens[1];
    // The regex is expected to match a quoted include.  If the user
    // didn't put quotes, assume they wanted a non-system file.
    if (!IsQuotedInclude(regex))
      regex = "\"(" + regex + ")\"";
    ERRSYM(this_file_entry) << GetFilePath(begin_loc)
                            << " adding friend regex " << regex << "\n";
    MutableGlobalIncludePicker()->AddFriendRegex(
        GetFilePath(begin_loc), regex);
    return;
  }

  if (MatchOneToken(tokens, "associated", 1, begin_loc)) {
    if (associated_pragma_location_.isInvalid()) {
      associated_pragma_location_ = begin_loc;
    }
    return;
  }

  if (MatchOneToken(tokens, "always_keep", 1, begin_loc)) {
    always_keep_files_.insert(this_file_entry);
    ERRSYM(this_file_entry)
        << "Marking include " << GetFilePath(this_file_entry)
        << " as always-keep\n";
    return;
  }

  // "keep" and "export" are handled in MaybeProtectInclude.
  if (!MatchOneToken(tokens, "keep", 1, begin_loc)
      && !MatchOneToken(tokens, "export", 1, begin_loc)) {
    Warn(this, begin_loc, "Unknown or malformed pragma (" + pragma_text + ")");
    return;
  }
}

void IwyuPreprocessorInfo::ProcessHeadernameDirectivesInFile(
    SourceLocation file_beginning) {
  SourceLocation current_loc = file_beginning;

  while (true) {
    // Figure out the canonical name of this file.  We can't use
    // GetFilePath() because it may not interact properly with -I.
    current_loc = GetLocationAfter(current_loc,
                                   "@file ",
                                   DefaultDataGetter());
    if (!current_loc.isValid()) {
      break;
    }
    const string filename = GetSourceTextUntilEndOfLine(current_loc,
                                                        DefaultDataGetter()).str();
    // Use "" or <> based on where the file lives.
    string quoted_private_include;
    if (IsSystemIncludeFile(GetFilePath(current_loc)))
      quoted_private_include = "<" + filename + ">";
    else
      quoted_private_include = "\"" + filename + "\"";

    // TODO(dsturtevant): Maybe place restrictions on the
    // placement. E.g., in a comment, before any code, or perhaps only
    // when in the same comment as an @file directive.
    current_loc = GetLocationAfter(current_loc,
                                   "@headername{",
                                   DefaultDataGetter());
    if (!current_loc.isValid()) {
      break;
    }

    string after_text = GetSourceTextUntilEndOfLine(current_loc,
                                                    DefaultDataGetter()).str();
    const string::size_type close_brace_pos = after_text.find('}');
    if (close_brace_pos == string::npos) {
      Warn(this, current_loc, "@headername directive missing a closing brace");
      continue;
    }
    after_text = after_text.substr(0, close_brace_pos);
    vector<string> public_includes = Split(after_text, ",", 0);

    for (string& public_include : public_includes) {
      StripWhiteSpace(&public_include);
      const string quoted_header_name = "<" + public_include + ">";

      VERRS(8) << "Adding dynamic mapping for @headername\n";
      MutableGlobalIncludePicker()->AddMapping(
          quoted_private_include, MappedInclude(quoted_header_name));
      MutableGlobalIncludePicker()->MarkIncludeAsPrivate(
          quoted_private_include);
    }
    break;  // No more than one @headername directive allowed.
  }
}

//------------------------------------------------------------
// Utilities for adding #includes.

// Helper function that returns iwyu_file_info_map_[file_entry] if
// it already exists, or creates a new one and returns it otherwise.
IwyuFileInfo* IwyuPreprocessorInfo::GetFromFileInfoMap(const FileEntry* file) {
  IwyuFileInfo* iwyu_file_info = FindInMap(&iwyu_file_info_map_, file);
  if (!iwyu_file_info) {
    const string quoted_include = ConvertToQuotedInclude(GetFilePath(file));
    iwyu_file_info_map_.insert(
        make_pair(file, IwyuFileInfo(file, this, quoted_include)));
    iwyu_file_info = FindInMap(&iwyu_file_info_map_, file);
    CHECK_(iwyu_file_info);   // should succeed this time!
  }
  return iwyu_file_info;
}

void IwyuPreprocessorInfo::InsertIntoFileInfoMap(
    const FileEntry* file, const string& quoted_include_name) {
  if (!FindInMap(&iwyu_file_info_map_, file)) {
    iwyu_file_info_map_.insert(
        make_pair(file, IwyuFileInfo(file, this, quoted_include_name)));
  }
}

void IwyuPreprocessorInfo::MaybeRecordDottedInlineExport(
    SourceLocation includer_loc, const FileEntry* includee,
    const string& include_name_as_written) {
  const FileEntry* includer = GetFileEntry(includer_loc);
  if (!includer || !includee || include_name_as_written.empty())
    return;
  const string includer_path = GetFilePath(includer);
  const string includee_path = GetFilePath(includee);
  if (!IsDottedInlineHeader(includer_path) ||
      !IsExactPlainCounterpart(includer_path, includee_path)) {
    return;
  }
  pending_dotted_inline_exports_.push_back(
      {includer, includee, include_name_as_written,
       GetOrDefault(conditional_stacks_, includer,
                    vector<ConditionalState>())});
}

void IwyuPreprocessorInfo::FinalizeDottedInlineExports() {
  set<pair<string, string>> added;
  for (const PendingDottedInlineExport& pending :
       pending_dotted_inline_exports_) {
    bool unconditional = pending.conditions.empty();
    if (pending.conditions.size() == 1) {
      const ConditionalState& condition = pending.conditions.front();
      clang::HeaderFileInfo& info =
          preprocessor_->getHeaderSearchInfo().getFileInfo(pending.includer);
      const clang::IdentifierInfo* controlling_macro =
          info.getControllingMacro(preprocessor_->getExternalSource());
      unconditional = !condition.in_alternative && controlling_macro &&
                      (!condition.macro ||
                       (condition.is_ifndef &&
                        condition.macro == controlling_macro));
    }
    if (!unconditional)
      continue;

    const string includer_path = GetFilePath(pending.includer);
    const string includee_path = GetFilePath(pending.includee);
    if (!added.insert({includee_path, includer_path}).second)
      continue;
    const string quoted_includer = ConvertToQuotedInclude(includer_path);
    const MappedInclude map_to(quoted_includer, includer_path);
    VERRS(8) << "Adding dotted-inline export mapping: "
             << pending.include_name_as_written << " -> "
             << quoted_includer << "\n";
    MutableGlobalIncludePicker()->RegisterVerifiedDottedInlineExport(
        includee_path, includer_path);
    MutableGlobalIncludePicker()->AddMapping(
        pending.include_name_as_written, map_to);
    const string context_free_includee = ConvertToQuotedInclude(includee_path);
    if (context_free_includee != pending.include_name_as_written) {
      MutableGlobalIncludePicker()->AddMapping(context_free_includee, map_to);
    }
  }
}

// Sometimes, we can tell just by looking at an #include line
// that iwyu should never recommend removing the #include.  For
// instance, if it has an IWYU pragma saying to keep it.
void IwyuPreprocessorInfo::MaybeProtectInclude(
    SourceLocation includer_loc, const FileEntry* includee,
    const string& include_name_as_written) {
  const FileEntry* includer = GetFileEntry(includer_loc);
  if (IsBuiltinOrCommandLineFile(includer))
    return;

  string protect_reason;
  // We always keep lines with pragmas "keep" or "export".
  // TODO(dsturtevant): As written "// // IWYU pragma: keep" is incorrectly
  // interpreted as a pragma. Maybe do "keep" and "export" pragma handling
  // in HandleComment?
  if (LineHasText(includer_loc, "// IWYU pragma: keep") ||
      LineHasText(includer_loc, "/* IWYU pragma: keep") ||
      HasOpenBeginKeep(includer)) {
    protect_reason = "pragma_keep";
    FileInfoFor(includer)->ReportKnownDesiredFile(
        includee, "pragma_keep",
        "IWYU pragma keep requires this present include to remain.");

  } else if (ShouldKeepIncludeFor(includee)) {
    // The command line version of pragma keep.
    protect_reason = "--keep";
    FileInfoFor(includer)->ReportKnownDesiredFile(
        includee, "command_line_keep",
        "The --keep option requires this present include to remain.");

  } else if (LineHasText(includer_loc, "// IWYU pragma: export") ||
             LineHasText(includer_loc, "/* IWYU pragma: export") ||
             HasOpenBeginExports(includer)) {
    protect_reason = "pragma_export";
    const string includer_path = GetFilePath(includer);
    const string quoted_includer = ConvertToQuotedInclude(includer_path);
    MappedInclude map_to(quoted_includer, includer_path);
    VERRS(8) << "Adding dynamic mapping for export pragma: "
             << "(" << GetFilePath(includee) << ") -> (" << includer_path
             << ")\n";
    MutableGlobalIncludePicker()->AddMapping(include_name_as_written, map_to);
    // Relative includes can be problematic as map keys, because they are
    // context-dependent.  Convert it to a context-free quoted include
    // (which may contain the full path to the file), and add that too.
    string map_from = ConvertToQuotedInclude(GetFilePath(includee));
    if (map_from != include_name_as_written) {
      VERRS(8) << "Adding dynamic mapping for export pragma (relative): "
               << "(" << GetFilePath(includee) << ") -> (" << includer_path
               << ")\n";
      MutableGlobalIncludePicker()->AddMapping(map_from, map_to);
    }
    FileInfoFor(includer)->ReportProtectedFileReason(
        includee, "pragma_export",
        "IWYU pragma export requires this include to remain for consumers.");

  // We also always keep #includes of .c files: iwyu doesn't touch those.
  // TODO(csilvers): instead of IsHeaderFile, check if the file has
  // any "non-inlined" definitions.
  } else if (!IsHeaderFile(GetFilePath(includee))) {
    protect_reason = ".cc include";
    FileInfoFor(includer)->ReportProtectedFileReason(
        includee, "source_include",
        "Includes of source files are outside IWYU removal semantics.");

  // If the includee is marked as pch-in-code, it can never be removed.
  } else if (FileInfoFor(includee)->is_pch_in_code()) {
    protect_reason = "pch in code";
    FileInfoFor(includer)->ReportProtectedFileReason(
        includee, "pch_in_code",
        "The include is the in-source precompiled-header marker.");

    // There's a few more places where we want to keep the #include, but we need
    // to finalize all #includes before we can test them, so we do it in a
    // separate function, FinalizeProtectedIncludes, below.
  }

  if (!protect_reason.empty()) {
    CHECK_(ContainsKey(iwyu_file_info_map_, includer));
    GetFromFileInfoMap(includer)->ReportIncludeFileUse(
        includee, include_name_as_written,
        OneUse::kProtectedIncludeSynthetic);
    ERRSYM(includer) << "Marked dep: " << GetFilePath(includer)
                     << " needs to keep " << include_name_as_written
                     << " (reason: " << protect_reason << ")\n";
  }
}

void IwyuPreprocessorInfo::FinalizeProtectedIncludes() {
  for (auto& entry : iwyu_file_info_map_) {
    const FileEntry* includer_file = entry.first;
    IwyuFileInfo& includer = entry.second;
    const string includer_path = GetFilePath(includer_file);

    for (const FileEntry* include : includer.direct_includes_as_fileentries()) {
      const string includee_path = GetFilePath(include);
      if (GlobalIncludePicker().HasMapping(includee_path, includer_path)) {
        // If includer re-exports includee, that counts as a "use" of it, so
        // protect the include.
        includer.ReportIncludeFileUse(
            include, ConvertToQuotedInclude(includee_path),
            OneUse::kProtectedIncludeSynthetic);
        includer.ReportProtectedFileReason(
            include, "reexported_include",
            "The includer re-exports this include to its consumers.");
        ERRSYM(includer_file)
            << "Marked dep: " << includer_path << " needs to keep "
            << includee_path << " (reason: re-exports)\n";
      } else if (always_keep_files_.count(include) > 0) {
        // Includee itself contains an "always_keep" pragma that protects it
        // from removal in all includers.
        includer.ReportIncludeFileUse(
            include, ConvertToQuotedInclude(includee_path),
            OneUse::kProtectedIncludeSynthetic);
        includer.ReportKnownDesiredFile(
            include, "pragma_always_keep",
            "IWYU pragma always_keep requires this include to remain.");
        ERRSYM(includer_file)
            << "Marked dep: " << includer_path << " needs to keep "
            << includee_path << " (reason: always_keep pragma)\n";
      }
    }
  }
}

// Called when a #include is encountered.  i_n_a_t includes <> or "".
// We keep track of this information in two places:
// 1) iwyu_file_info_map_ maps the includer as a FileEntry* to the
//    includee both as the literal name used and as a FileEntry*.
// 2) include_to_fileentry_map_ maps the includee's literal name
//    as written to the FileEntry* used.  This can be used (in a
//    limited way, due to non-uniqueness concerns) to map between
//    names and FileEntries.
// We also tell this #include to the include-picker, which may
// use it to fine-tune its include-picking algorithms.
void IwyuPreprocessorInfo::AddDirectInclude(
    SourceLocation includer_loc, const FileEntry* includee,
    const string& include_name_as_written, FileID includee_file_id) {
  if (IsBuiltinOrCommandLineFile(includee))
    return;

  // For files we're going to be reporting IWYU errors for, we need
  // both forms of the includee to be specified.  For other files, we
  // don't care as much.
  const FileEntry* includer = GetFileEntry(includer_loc);
  if (includer && includer_loc.isValid()) {
    direct_include_conditions_.push_back(
        {includer, includee, include_name_as_written, includer_loc,
         includee_file_id});
  }
  if (ShouldReportIWYUViolationsFor(includer)) {
    CHECK_(includee != nullptr);
    CHECK_(!include_name_as_written.empty());
  }
  ++num_includes_seen_[includer];

  GetFromFileInfoMap(includer)->AddInclude(
      includee, include_name_as_written, GetLineNumber(includer_loc),
      GetSourceLineAsWritten(includer_loc));
  // Make sure the includee has a file-info-map entry too.
  InsertIntoFileInfoMap(includee, include_name_as_written);

  // The first #include in every translation unit might be a precompiled header
  // and we need to mark it as such for later analysis.
  bool is_includer_main_compilation_unit = main_file_ && includer == main_file_;
  if (is_includer_main_compilation_unit && num_includes_seen_[includer] == 1) {
    CHECK_(includee && "The first #include must be an actual file.");

    // Now we know includee is the first included header file. Mark it as
    // pch-in-code if the user requested it on command-line.
    if (GlobalFlags().pch_in_code) {
      IwyuFileInfo *includee_file_info = GetFromFileInfoMap(includee);
      includee_file_info->set_pch_in_code();
      includee_file_info->set_prefix_header();
      VERRS(4) << "Marked " << GetFilePath(includee) << " as pch-in-code.\n";
    }
  }

  // We have a rule that if foo.h #includes bar.h, foo.cc doesn't need
  // to #include bar.h as well, but instead gets it 'automatically'
  // via foo.h.  We say that 'foo.h' is an "associated header" for
  // foo.cc.  Make sure we ignore self-includes, though!
  // iwyu_output.cc gets upset if a file is its own associated header.
  if (includer == main_file_ && includee != includer &&
      BelongsToMainCompilationUnit(includer, includee)) {
    IwyuFileInfo* includer_info = GetFromFileInfoMap(includer);
    IwyuFileInfo* includee_info = GetFromFileInfoMap(includee);
    const string includee_path = GetFilePath(includee);
    const bool canonical_match =
        GetCanonicalName(includee_path) ==
        GetCanonicalName(GetFilePath(main_file_));
    if (canonical_match && IsInlineHeaderFile(includee_path)) {
      includer_info->AddAssociatedHeader(
          includee_info, "inline_definitions", "canonical_inline_stem",
          "Inline-definition header matches the translation-unit stem.");
    } else if (canonical_match) {
      includer_info->AddAssociatedHeader(
          includee_info, "declarations", "canonical_stem",
          "Declaration header matches the translation-unit stem.");
    } else if (GlobalFlags().pch_in_code &&
               num_includes_seen_[includer] == 2) {
      includer_info->AddAssociatedHeader(
          includee_info, "heuristic", "pch_following_include_basename",
          "Include following the in-source PCH marker matches the "
          "translation-unit basename.");
    } else {
      includer_info->AddAssociatedHeader(
          includee_info, "heuristic", "first_include_basename",
          "First include matches the translation-unit basename.");
    }
    VERRS(4) << "Marked " << GetFilePath(includee)
             << " as associated header of " << GetFilePath(includer) << ".\n";

    // All associated headers need to be included in IWYU analysis.
    // We can only get here if IWYU is invoked with an absolute source path and
    // its associated header is included by two different path names (e.g.
    // "rel/path/assoc.h" and "assoc.h") in different files.
    //
    // TODO: This line cannot be covered with our current test framework;
    // don't forget to add a test case if we build something better in the
    // future.
    AddGlobToReportIWYUViolationsFor(GetFilePath(includee));
  }

  // Besides marking headers as "associated header" with heuristics, the user
  // can directly mark headers with the associated pragma.
  const FileEntry* associated_includer =
      GetFileEntry(associated_pragma_location_);
  if (associated_pragma_location_.isValid() &&
      associated_includer == includer) {
    GetFromFileInfoMap(includer)->AddAssociatedHeader(
        GetFromFileInfoMap(includee), "explicit", "pragma_associated",
        "Header was marked by an IWYU associated pragma.");
    VERRS(4) << "Marked " << GetFilePath(includee)
             << " as associated header of " << GetFilePath(includer)
             << " due to associated pragma.\n";

    AddGlobToReportIWYUViolationsFor(GetFilePath(includee));
    associated_pragma_location_ = SourceLocation();
  }

  // Also keep track of what FileEntry we ended up using for this name.
  // Because we use #include-next, the same include-name can map to
  // several files; we use the first such mapping we see, which is the
  // top of the #include-next chain.
  if (!include_name_as_written.empty()) {
    if (!ContainsKey(include_to_fileentry_map_, include_name_as_written)) {
      include_to_fileentry_map_[include_name_as_written] = includee;
    }
  }

  // Tell the include-picker about this new include.
  MutableGlobalIncludePicker()->AddDirectInclude(
      GetFilePath(includer), GetFilePath(includee), include_name_as_written);

  MaybeRecordDottedInlineExport(includer_loc, includee,
                                include_name_as_written);

  MaybeProtectInclude(includer_loc, includee, include_name_as_written);

  ERRSYM(includer) << "Added an #include: " << GetFilePath(includer)
                   << " -> " << include_name_as_written << "\n";
}

//------------------------------------------------------------
// Preprocessor event handlers.

// Called whenever a macro is expanded.  Example: when FOO(a, b) is
// seen in the source code, where FOO() is a macro #defined earlier,
// MacroExpands() will be called once with 'macro_use_token' being
// FOO, and 'directive' containing more information about FOO's
// definition.
void IwyuPreprocessorInfo::MacroExpands(const Token& macro_use_token,
                                        const MacroDefinition& definition,
                                        SourceRange range,
                                        const clang::MacroArgs* /*args*/) {
  const FileEntry* macro_file = GetFileEntry(macro_use_token);
  const MacroInfo* macro_def = definition.getMacroInfo();
  if (ShouldPrintSymbolFromFile(macro_file)) {
    errs() << "[ Use macro   ] "
           << PrintableLoc(macro_use_token.getLocation())
           << ": " << GetName(macro_use_token) << " "
           << "(from " << PrintableLoc(macro_def->getDefinitionLoc()) << ")\n";
  }

  ReportMacroUse(GetName(macro_use_token),
                 macro_use_token.getLocation(),
                 macro_def->getDefinitionLoc());
}

void IwyuPreprocessorInfo::MacroDefined(const Token& id,
                                        const MacroDirective* directive) {
  const MacroInfo* macro = directive->getMacroInfo();
  const SourceLocation macro_loc = macro->getDefinitionLoc();
  ERRSYM(GetFileEntry(macro_loc))
      << "[ #define     ] " << PrintableLoc(macro_loc)
      << ": " << GetName(id) << "\n";
  // We'd like to do an iwyu check on every token in the macro
  // definition, but without knowing how and where the macro will be
  // used, we don't have enough context to.  But we *can* check those
  // tokens that are macro calls: that is, one macro calling another.
  // We can't do the checking as we go, since macros can refer to
  // macros that come after them in the source file, so we just store
  // every macro that's defined, and every macro it calls from its
  // body, and then after reading the whole file we do an iwyu
  // analysis on the results.  (This can make mistakes if the code
  // #undefs and re-defines a macro, but should work fine in practice.)
  if (macro_loc.isValid())
    macros_definition_loc_[GetName(id)] = macro_loc;

  const string macro_name = GetName(id);
  // A direct redefinition in the same entered file instance supersedes the
  // prior definition even without an intervening #undef. A definition in a
  // downstream file does not retire an exported header's earlier record.
  if (macro_loc.isValid()) {
    const auto& source_manager = preprocessor_->getSourceManager();
    const SourceLocation new_loc = source_manager.getSpellingLoc(macro_loc);
    if (new_loc.isValid() && new_loc.isFileID()) {
      for (auto previous = macro_body_dependencies_.rbegin();
           previous != macro_body_dependencies_.rend(); ++previous) {
        if (previous->macro_name != macro_name ||
            previous->retired_in_defining_instance) {
          continue;
        }
        const SourceLocation previous_loc = source_manager.getSpellingLoc(
            previous->definition_location);
        if (previous_loc.isValid() && previous_loc.isFileID() &&
            source_manager.getFileID(previous_loc) ==
                source_manager.getFileID(new_loc)) {
          previous->retired_in_defining_instance = true;
        }
        break;
      }
    }
  }

  if (macro_loc.isValid() && macro->isFunctionLike()) {
    const set<const clang::IdentifierInfo*> parameters(macro->param_begin(),
                                                        macro->param_end());
    MacroBodyDependency body{macro, macro_loc, macro_name, {}, false};
    set<string> seen;
    const llvm::ArrayRef<Token> tokens = macro->tokens();
    for (size_t index = 0; index < tokens.size(); ++index) {
      const Token& token = tokens[index];
      if (!token.is(clang::tok::identifier))
        continue;

      // A token following '.' or '->' is selected from the expansion-time
      // receiver. It is not a declaration dependency of the macro header.
      if (index > 0 && (tokens[index - 1].is(clang::tok::period) ||
                        tokens[index - 1].is(clang::tok::arrow))) {
        continue;
      }

      // Parse each nested-name-specifier only once, from its first component.
      if (index >= 2 && tokens[index - 1].is(clang::tok::coloncolon) &&
          tokens[index - 2].is(clang::tok::identifier)) {
        continue;
      }

      MacroBodyReference reference{{}, token.getLocation(), false, false};
      if (index > 0 && tokens[index - 1].is(clang::tok::coloncolon) &&
          (index < 2 || !tokens[index - 2].is(clang::tok::identifier))) {
        reference.globally_qualified = true;
      }

      size_t current = index;
      bool contains_parameter = false;
      string spelling = reference.globally_qualified ? "::" : "";
      for (;;) {
        const clang::IdentifierInfo* identifier =
            tokens[current].getIdentifierInfo();
        if (!identifier)
          break;
        contains_parameter = contains_parameter ||
                             ContainsKey(parameters, identifier);
        reference.was_macro = reference.was_macro ||
                              identifier->hasMacroDefinition();
        reference.components.push_back(identifier);
        if (reference.components.size() > 1)
          spelling += "::";
        spelling += identifier->getName().str();
        if (current + 2 >= tokens.size() ||
            !tokens[current + 1].is(clang::tok::coloncolon) ||
            !tokens[current + 2].is(clang::tok::identifier)) {
          break;
        }
        current += 2;
      }
      index = current;
      if (!contains_parameter && seen.insert(spelling).second)
        body.references.push_back(std::move(reference));
    }
    macro_body_dependencies_.push_back(std::move(body));
  }

  for (const Token& token_in_macro : macro->tokens()) {
    if (token_in_macro.getKind() == clang::tok::identifier &&
        token_in_macro.getIdentifierInfo()->hasMacroDefinition()) {
      macros_called_from_macros_.push_back(token_in_macro);
    }
  }
}

void IwyuPreprocessorInfo::MacroUndefined(
    const Token& id, const MacroDefinition& definition,
    const MacroDirective* /*directive*/) {
  const MacroInfo* macro = definition.getMacroInfo();
  if (!macro)
    return;

  const auto& source_manager = preprocessor_->getSourceManager();
  SourceLocation undef_loc = source_manager.getSpellingLoc(id.getLocation());
  SourceLocation definition_loc =
      source_manager.getSpellingLoc(macro->getDefinitionLoc());
  if (undef_loc.isInvalid() || definition_loc.isInvalid() ||
      !undef_loc.isFileID() || !definition_loc.isFileID() ||
      source_manager.getFileID(undef_loc) !=
          source_manager.getFileID(definition_loc)) {
    return;
  }

  for (MacroBodyDependency& body : macro_body_dependencies_) {
    if (body.macro_info == macro)
      body.retired_in_defining_instance = true;
  }
}

string IwyuPreprocessorInfo::ConditionSourceText(SourceRange range) const {
  if (range.isInvalid())
    return "";
  bool invalid = false;
  const auto& source_manager = preprocessor_->getSourceManager();
  const StringRef source = clang::Lexer::getSourceText(
      clang::CharSourceRange::getTokenRange(range), source_manager,
      preprocessor_->getLangOpts(), &invalid);
  if (invalid)
    return "";

  // Translation phase 2 removes escaped physical newlines before comments are
  // recognized. Preserve all other bytes for the quote-aware comment pass.
  string joined;
  for (size_t index = 0; index < source.size(); ++index) {
    if (source[index] == '\\' && index + 1 < source.size() &&
        source[index + 1] == '\n') {
      ++index;
      continue;
    }
    if (source[index] == '\\' && index + 2 < source.size() &&
        source[index + 1] == '\r' && source[index + 2] == '\n') {
      index += 2;
      continue;
    }
    joined.push_back(source[index]);
  }

  // Translation phase 3 replaces comments with whitespace. Do not interpret
  // comment markers inside character or string literals.
  string tokens;
  char quote = '\0';
  bool escaped = false;
  for (size_t index = 0; index < joined.size(); ++index) {
    const char ch = joined[index];
    if (quote != '\0') {
      tokens.push_back(ch);
      if (escaped) {
        escaped = false;
      } else if (ch == '\\') {
        escaped = true;
      } else if (ch == quote) {
        quote = '\0';
      }
      continue;
    }
    if (ch == '\'' || ch == '"') {
      quote = ch;
      tokens.push_back(ch);
      continue;
    }
    if (ch == '/' && index + 1 < joined.size() && joined[index + 1] == '/') {
      tokens.push_back(' ');
      break;
    }
    if (ch == '/' && index + 1 < joined.size() && joined[index + 1] == '*') {
      const size_t end = joined.find("*/", index + 2);
      if (end == string::npos)
        return "";
      tokens.push_back(' ');
      index = end + 1;
      continue;
    }
    tokens.push_back(ch);
  }
  if (quote != '\0')
    return "";

  string normalized;
  bool pending_space = false;
  for (const unsigned char ch : tokens) {
    if (std::isspace(ch)) {
      pending_space = !normalized.empty();
      continue;
    }
    if (pending_space)
      normalized.push_back(' ');
    normalized.push_back(static_cast<char>(ch));
    pending_space = false;
  }
  return normalized;
}

void IwyuPreprocessorInfo::RecordConditionEvent(SourceLocation loc) {
  const auto& source_manager = preprocessor_->getSourceManager();
  loc = source_manager.getExpansionLoc(loc);
  if (loc.isInvalid() || !loc.isFileID())
    return;

  const FileID file_id = source_manager.getFileID(loc);
  if (file_id.isInvalid())
    return;
  const unsigned key = file_id.getHashValue();
  condition_events_[key].push_back(
      {key, source_manager.getFileOffset(loc), active_condition_stacks_[key]});
}

bool IwyuPreprocessorInfo::ConditionPathAt(
    SourceLocation loc, vector<ActiveConditionalState>* path) const {
  CHECK_(path);
  path->clear();

  const auto& source_manager = preprocessor_->getSourceManager();
  loc = source_manager.getExpansionLoc(loc);
  if (loc.isInvalid() || !loc.isFileID())
    return false;

  const FileID file_id = source_manager.getFileID(loc);
  if (file_id.isInvalid())
    return false;
  const unsigned key = file_id.getHashValue();
  const unsigned offset = source_manager.getFileOffset(loc);

  const auto events_it = condition_events_.find(key);
  if (events_it == condition_events_.end())
    return true;
  const vector<ConditionEvent>& events = events_it->second;
  const auto after = std::upper_bound(
      events.begin(), events.end(), offset,
      [](unsigned value, const ConditionEvent& event) {
        return value < event.offset;
      });
  if (after != events.begin())
    *path = std::prev(after)->path;
  return true;
}

IwyuPreprocessorInfo::BranchActivity
IwyuPreprocessorInfo::ActivityUnderParent(
    BranchActivity condition_activity, BranchActivity parent_activity) const {
  if (parent_activity == BranchActivity::kInactive)
    return BranchActivity::kInactive;
  if (parent_activity == BranchActivity::kUnknown)
    return BranchActivity::kUnknown;
  return condition_activity;
}

void IwyuPreprocessorInfo::ObserveExpandedToken(const Token& token) {
  if (token.is(clang::tok::l_brace)) {
    ++expanded_brace_depth_;
  } else if (token.is(clang::tok::r_brace)) {
    if (expanded_brace_depth_ == 0) {
      expanded_scope_unknown_ = true;
    } else {
      --expanded_brace_depth_;
    }
  }
}

void IwyuPreprocessorInfo::ClassifyConditionalRegionScopes(
    clang::ASTContext& context) {
  struct FileRange {
    unsigned file_id;
    unsigned begin_offset;
    unsigned end_offset;
  };

  class ScopeRangeVisitor
      : public clang::RecursiveASTVisitor<ScopeRangeVisitor> {
   public:
    ScopeRangeVisitor(clang::SourceManager& source_manager,
                      const clang::LangOptions& language_options,
                      vector<FileRange>* ranges)
        : source_manager_(source_manager),
          language_options_(language_options), ranges_(ranges) {}

    bool VisitDecl(clang::Decl* declaration) {
      if (declaration && !llvm::isa<clang::TranslationUnitDecl>(declaration))
        AddRange(declaration->getSourceRange());
      return true;
    }

    bool VisitStmt(clang::Stmt* statement) {
      if (statement)
        AddRange(statement->getSourceRange());
      return true;
    }

    bool VisitTypeLoc(clang::TypeLoc type_location) {
      if (!type_location.isNull())
        AddRange(type_location.getSourceRange());
      return true;
    }

   private:
    void AddRange(SourceRange range) {
      SourceLocation begin = source_manager_.getExpansionLoc(range.getBegin());
      SourceLocation end = source_manager_.getExpansionLoc(range.getEnd());
      if (begin.isInvalid() || end.isInvalid() || !begin.isFileID() ||
          !end.isFileID()) {
        return;
      }
      const FileID begin_file = source_manager_.getFileID(begin);
      if (begin_file.isInvalid() || begin_file != source_manager_.getFileID(end))
        return;
      SourceLocation after_end = clang::Lexer::getLocForEndOfToken(
          end, 0, source_manager_, language_options_);
      if (after_end.isInvalid() || !after_end.isFileID() ||
          source_manager_.getFileID(after_end) != begin_file) {
        return;
      }
      const unsigned begin_offset = source_manager_.getFileOffset(begin);
      const unsigned end_offset = source_manager_.getFileOffset(after_end);
      if (begin_offset < end_offset) {
        ranges_->push_back(
            {begin_file.getHashValue(), begin_offset, end_offset});
      }
    }

    clang::SourceManager& source_manager_;
    const clang::LangOptions& language_options_;
    vector<FileRange>* ranges_;
  };

  vector<FileRange> ranges;
  ScopeRangeVisitor visitor(context.getSourceManager(), context.getLangOpts(),
                            &ranges);
  visitor.TraverseDecl(context.getTranslationUnitDecl());

  for (ConditionalRegion& region : conditional_regions_) {
    if (region.lexical_scope != LexicalScope::kFile)
      continue;
    for (const FileRange& range : ranges) {
      if (range.file_id == region.file_id &&
          range.begin_offset <= region.directive_offset &&
          region.directive_offset < range.end_offset) {
        region.lexical_scope = LexicalScope::kNonFile;
        break;
      }
    }
  }
}

SourceLocation IwyuPreprocessorInfo::DirectiveKeywordLocation(
    SourceLocation loc, const string& directive_kind) const {
  const auto& source_manager = preprocessor_->getSourceManager();
  loc = source_manager.getExpansionLoc(loc);
  if (loc.isInvalid() || !loc.isFileID())
    return loc;
  const FileID file_id = source_manager.getFileID(loc);
  bool invalid = false;
  const StringRef buffer = source_manager.getBufferData(file_id, &invalid);
  if (invalid)
    return loc;
  const unsigned callback_offset = source_manager.getFileOffset(loc);
  for (const RawConditionalDirective& directive :
       ScanConditionalDirectives(buffer)) {
    if (directive.kind == directive_kind &&
        directive.hash_offset <= callback_offset &&
        callback_offset <= directive.directive_offset) {
      return source_manager.getLocForStartOfFile(file_id).getLocWithOffset(
          directive.directive_offset);
    }
  }
  return loc;
}

unsigned IwyuPreprocessorInfo::DirectiveBodyBeginOffset(
    SourceLocation loc) const {
  const auto& source_manager = preprocessor_->getSourceManager();
  loc = source_manager.getExpansionLoc(loc);
  if (loc.isInvalid() || !loc.isFileID())
    return 0;
  bool invalid = false;
  const FileID file_id = source_manager.getFileID(loc);
  const StringRef buffer = source_manager.getBufferData(file_id, &invalid);
  if (invalid)
    return 0;
  size_t offset = source_manager.getFileOffset(loc);
  while (offset < buffer.size()) {
    if (buffer[offset] != '\n') {
      ++offset;
      continue;
    }
    size_t slash = offset;
    if (slash > 0 && buffer[slash - 1] == '\r')
      --slash;
    const size_t line_end = slash;
    while (slash > 0 && buffer[slash - 1] == '\\')
      --slash;
    if ((line_end - slash) % 2 == 0)
      return static_cast<unsigned>(offset + 1);
    ++offset;
  }
  return static_cast<unsigned>(buffer.size());
}

void IwyuPreprocessorInfo::CloseCurrentRegion(unsigned file_id,
                                               unsigned end_offset) {
  auto stack_it = active_condition_stacks_.find(file_id);
  if (stack_it == active_condition_stacks_.end() || stack_it->second.empty())
    return;
  const size_t index = stack_it->second.back().region_fact_index;
  if (index >= conditional_regions_.size())
    return;
  ConditionalRegion& region = conditional_regions_[index];
  region.body_end_offset = end_offset;
  region.has_body_end = true;
}

void IwyuPreprocessorInfo::EnterConditional(
    SourceLocation loc, const clang::IdentifierInfo* macro, bool is_ifndef,
    const string& expression, BranchActivity condition_activity,
    const string& directive_kind) {
  loc = DirectiveKeywordLocation(loc, directive_kind);
  const FileEntry* file = GetFileEntry(loc);
  if (!file)
    return;
  conditional_stacks_[file].push_back({macro, is_ifndef, false});

  const auto& source_manager = preprocessor_->getSourceManager();
  const SourceLocation file_loc = source_manager.getExpansionLoc(loc);
  if (file_loc.isInvalid() || !file_loc.isFileID())
    return;
  const FileID file_id = source_manager.getFileID(file_loc);
  if (file_id.isInvalid())
    return;
  const unsigned key = file_id.getHashValue();
  const unsigned opening_offset = source_manager.getFileOffset(file_loc);
  vector<ActiveConditionalState>& active_stack = active_condition_stacks_[key];
  const BranchActivity parent_activity = active_stack.empty()
      ? BranchActivity::kActive : active_stack.back().activity;
  const BranchActivity activity =
      ActivityUnderParent(condition_activity, parent_activity);
  const LexicalScope lexical_scope =
      (!active_stack.empty() &&
       active_stack.back().activity != BranchActivity::kActive)
          ? LexicalScope::kUnknown
          : (expanded_scope_unknown_
                 ? LexicalScope::kUnknown
                 : (expanded_brace_depth_ == 0 ? LexicalScope::kFile
                                                : LexicalScope::kNonFile));
  const string parent_id = active_stack.empty()
      ? string() : active_stack.back().source_region_id;
  const string source_region_id =
      "branch-" + to_string(opening_offset) + "-0-" +
      to_string(opening_offset);
  const size_t fact_index = conditional_regions_.size();
  conditional_regions_.push_back(
      {source_region_id, key, opening_offset, opening_offset,
       DirectiveBodyBeginOffset(file_loc), 0, false, 0, parent_id,
       directive_kind, expression, activity, lexical_scope, file_loc, file_loc});
  conditional_regions_by_file_[file].push_back(fact_index);
  active_stack.push_back(
      {key, opening_offset, 0, expression, file_loc, {expression}, macro,
       is_ifndef, parent_activity, activity == BranchActivity::kActive,
       activity == BranchActivity::kUnknown, activity, lexical_scope,
       source_region_id, fact_index});
  RecordConditionEvent(file_loc);
}

void IwyuPreprocessorInfo::EnterAlternative(
    SourceLocation loc, const string& expression, bool is_else,
    BranchActivity condition_activity, const string& directive_kind) {
  loc = DirectiveKeywordLocation(loc, directive_kind);
  const FileEntry* file = GetFileEntry(loc);
  if (!file)
    return;
  vector<ConditionalState>& stack = conditional_stacks_[file];
  if (!stack.empty())
    stack.back().in_alternative = true;

  const auto& source_manager = preprocessor_->getSourceManager();
  const SourceLocation file_loc = source_manager.getExpansionLoc(loc);
  if (file_loc.isInvalid() || !file_loc.isFileID())
    return;
  const FileID file_id = source_manager.getFileID(file_loc);
  if (file_id.isInvalid())
    return;
  const unsigned key = file_id.getHashValue();
  vector<ActiveConditionalState>& active_stack = active_condition_stacks_[key];
  if (active_stack.empty())
    return;

  const unsigned directive_offset = source_manager.getFileOffset(file_loc);
  CloseCurrentRegion(key, directive_offset);
  ActiveConditionalState& state = active_stack.back();
  ++state.branch_index;
  string branch_expression;
  for (const string& prior : state.prior_branch_expressions) {
    if (prior.empty()) {
      branch_expression.clear();
      break;
    }
    if (!branch_expression.empty())
      branch_expression += " && ";
    branch_expression += "!(" + prior + ")";
  }
  if (!is_else) {
    if (expression.empty()) {
      branch_expression.clear();
    } else {
      if (!branch_expression.empty())
        branch_expression += " && ";
      branch_expression += "(" + expression + ")";
    }
    state.prior_branch_expressions.push_back(expression);
  }

  BranchActivity activity = BranchActivity::kUnknown;
  if (state.parent_activity == BranchActivity::kInactive || state.prior_active) {
    activity = BranchActivity::kInactive;
  } else if (state.parent_activity == BranchActivity::kUnknown ||
             state.prior_unknown) {
    activity = BranchActivity::kUnknown;
  } else if (is_else) {
    activity = BranchActivity::kActive;
  } else {
    activity = ActivityUnderParent(condition_activity, state.parent_activity);
  }
  state.prior_active = state.prior_active ||
                       activity == BranchActivity::kActive;
  state.prior_unknown = state.prior_unknown ||
                        activity == BranchActivity::kUnknown;
  state.expression = branch_expression;
  state.directive_location = file_loc;
  state.activity = activity;
  state.source_region_id =
      "branch-" + to_string(state.opening_location) + "-" +
      to_string(state.branch_index) + "-" + to_string(directive_offset);
  const string parent_id = active_stack.size() > 1
      ? active_stack[active_stack.size() - 2].source_region_id : string();
  const SourceLocation opening_location =
      conditional_regions_[state.region_fact_index].opening_location;
  state.region_fact_index = conditional_regions_.size();
  conditional_regions_.push_back(
      {state.source_region_id, key, state.opening_location, directive_offset,
       DirectiveBodyBeginOffset(file_loc), 0, false, state.branch_index,
       parent_id, directive_kind, branch_expression, activity,
       state.lexical_scope, opening_location, file_loc});
  conditional_regions_by_file_[file].push_back(state.region_fact_index);
  RecordConditionEvent(file_loc);
}

void IwyuPreprocessorInfo::ExitConditional(SourceLocation loc) {
  loc = DirectiveKeywordLocation(loc, "endif");
  const FileEntry* file = GetFileEntry(loc);
  if (!file)
    return;
  vector<ConditionalState>& stack = conditional_stacks_[file];
  if (!stack.empty())
    stack.pop_back();

  const auto& source_manager = preprocessor_->getSourceManager();
  const SourceLocation file_loc = source_manager.getExpansionLoc(loc);
  if (file_loc.isInvalid() || !file_loc.isFileID())
    return;
  const FileID file_id = source_manager.getFileID(file_loc);
  if (file_id.isInvalid())
    return;
  const unsigned key = file_id.getHashValue();
  vector<ActiveConditionalState>& active_stack = active_condition_stacks_[key];
  if (!active_stack.empty()) {
    CloseCurrentRegion(key, source_manager.getFileOffset(file_loc));
    active_stack.pop_back();
    RecordConditionEvent(file_loc);
  }
}

void IwyuPreprocessorInfo::If(
    SourceLocation loc, SourceRange condition_range,
    ConditionValueKind condition_value) {
  const BranchActivity activity =
      condition_value == ConditionValueKind::CVK_True
          ? BranchActivity::kActive
          : (condition_value == ConditionValueKind::CVK_False
                 ? BranchActivity::kInactive : BranchActivity::kUnknown);
  EnterConditional(loc, nullptr, false, ConditionSourceText(condition_range),
                   activity, "if");
}

void IwyuPreprocessorInfo::Elif(
    SourceLocation loc, SourceRange condition_range,
    ConditionValueKind condition_value, SourceLocation /*if_loc*/) {
  const BranchActivity activity =
      condition_value == ConditionValueKind::CVK_True
          ? BranchActivity::kActive
          : (condition_value == ConditionValueKind::CVK_False
                 ? BranchActivity::kInactive : BranchActivity::kUnknown);
  EnterAlternative(loc, ConditionSourceText(condition_range), false, activity,
                   "elif");
}

void IwyuPreprocessorInfo::Elifdef(
    SourceLocation loc, const Token& id,
    const MacroDefinition& definition) {
  EnterAlternative(loc, "defined(" + GetName(id) + ")", false,
                   definition.getMacroInfo() ? BranchActivity::kActive
                                             : BranchActivity::kInactive,
                   "elifdef");
}

void IwyuPreprocessorInfo::Elifdef(
    SourceLocation loc, SourceRange condition_range,
    SourceLocation /*if_loc*/) {
  EnterAlternative(loc, ConditionSourceText(condition_range), false,
                   BranchActivity::kUnknown, "elifdef");
}

void IwyuPreprocessorInfo::Elifndef(
    SourceLocation loc, const Token& id,
    const MacroDefinition& definition) {
  EnterAlternative(loc, "!defined(" + GetName(id) + ")", false,
                   definition.getMacroInfo() ? BranchActivity::kInactive
                                             : BranchActivity::kActive,
                   "elifndef");
}

void IwyuPreprocessorInfo::Elifndef(
    SourceLocation loc, SourceRange condition_range,
    SourceLocation /*if_loc*/) {
  EnterAlternative(loc, ConditionSourceText(condition_range), false,
                   BranchActivity::kUnknown, "elifndef");
}

void IwyuPreprocessorInfo::Else(
    SourceLocation loc, SourceLocation /*if_loc*/) {
  EnterAlternative(loc, "", true, BranchActivity::kUnknown, "else");
}

void IwyuPreprocessorInfo::Endif(
    SourceLocation loc, SourceLocation /*if_loc*/) {
  ExitConditional(loc);
}

void IwyuPreprocessorInfo::Ifdef(SourceLocation loc,
                                 const Token& id,
                                 const MacroDefinition& definition) {
  EnterConditional(loc, id.getIdentifierInfo(), false,
                   "defined(" + GetName(id) + ")",
                   definition.getMacroInfo() ? BranchActivity::kActive
                                             : BranchActivity::kInactive,
                   "ifdef");
  ERRSYM(GetFileEntry(id.getLocation()))
      << "[ #ifdef      ] " << PrintableLoc(id.getLocation())
      << ": " << GetName(id) << "\n";
  FindAndReportMacroUse(GetName(id), id.getLocation());
}

void IwyuPreprocessorInfo::Ifndef(SourceLocation loc,
                                  const Token& id,
                                  const MacroDefinition& definition) {
  EnterConditional(loc, id.getIdentifierInfo(), true,
                   "!defined(" + GetName(id) + ")",
                   definition.getMacroInfo() ? BranchActivity::kInactive
                                             : BranchActivity::kActive,
                   "ifndef");
  ERRSYM(GetFileEntry(id.getLocation()))
      << "[ #ifndef     ] " << PrintableLoc(id.getLocation())
      << ": " << GetName(id) << "\n";
  FindAndReportMacroUse(GetName(id), id.getLocation());
}

// Clang will give a MacroExpands() callback for all macro-tokens
// used inside an #if or #elif, *except* macro-tokens used within a
// 'defined' operator. They produce a Defined() callback.
void IwyuPreprocessorInfo::Defined(const Token& id,
                                   const MacroDefinition& /*definition*/,
                                   SourceRange /*range*/) {
  ERRSYM(GetFileEntry(id.getLocation()))
      << "[ #if defined ] " << PrintableLoc(id.getLocation())
      << ": " << GetName(id) << "\n";
  FindAndReportMacroUse(GetName(id), id.getLocation());
}

void IwyuPreprocessorInfo::InclusionDirective(
    SourceLocation hash_loc,
    const Token& include_token,
    StringRef filename,
    bool is_angled,
    clang::CharSourceRange filename_range,
    clang::OptionalFileEntryRef file,
    StringRef search_path,
    StringRef relative_path,
    const clang::Module* imported,
    SrcMgr::CharacteristicKind file_type) {
  include_filename_loc_ = filename_range.getBegin();
}

void IwyuPreprocessorInfo::FileChanged(SourceLocation loc,
                                       FileChangeReason reason,
                                       SrcMgr::CharacteristicKind file_type,
                                       FileID exiting_from_id) {
  switch (reason) {
    case EnterFile:
      FileChanged_EnterFile(loc);
      return;
    case ExitFile:
      FileChanged_ExitToFile(
          loc, GlobalSourceManager()->getFileEntryForID(exiting_from_id));
      return;
    case RenameFile:
      FileChanged_RenameFile(loc);
      return;
    case SystemHeaderPragma:
      // We see "#pragma GCC system_header".
      FileChanged_SystemHeaderPragma(loc);
      return;
  }
  CHECK_UNREACHABLE_("Unknown file change reason");
}

// Called when we see an #include, but decide we don't need to
// actually read it because it's already been #included (and is
// protected by a header guard).
void IwyuPreprocessorInfo::FileSkipped(const FileEntryRef& file,
                                       const Token &filename,
                                       SrcMgr::CharacteristicKind file_type) {
  CHECK_(include_filename_loc_.isValid() &&
         "Must skip file only for actual inclusion directive");
  const string include_name_as_written =
      GetIncludeNameAsWritten(include_filename_loc_);
  const SourceLocation include_loc =
      GetInstantiationLoc(filename.getLocation());
  ERRSYM(GetFileEntry(include_loc))
      << "[ (#include)  ] " << include_name_as_written
      << " (" << GetFilePath(&file.getFileEntry()) << ")\n";

  AddDirectInclude(include_loc, &file.getFileEntry(), include_name_as_written);
  if (ShouldReportIWYUViolationsFor(&file.getFileEntry())) {
    files_to_report_iwyu_violations_for_.insert(&file.getFileEntry());
  }
}

// Called when a file is #included.
void IwyuPreprocessorInfo::FileChanged_EnterFile(
    SourceLocation file_beginning) {
  // Get the location of the #include directive that resulted in the
  // include of the file that file_beginning is in.
  const SourceLocation include_loc = GlobalSourceManager()->getIncludeLoc(
      GlobalSourceManager()->getFileID(file_beginning));
  string include_name_as_written;
  if (!IsBuiltinOrCommandLineFile(GetFileEntry(include_loc))) {
    CHECK_(include_filename_loc_.isValid() &&
           "Include from not built-in file must have inclusion directive");
    include_name_as_written = GetIncludeNameAsWritten(include_filename_loc_);
  }
  ERRSYM(GetFileEntry(include_loc))
      << "[ #include    ] " << include_name_as_written
      << " (" << GetFilePath(file_beginning) << ")\n";

  const FileEntry* const new_file = GetFileEntry(file_beginning);
  if (new_file) {
    entered_file_ids_[new_file].push_back(
        GlobalSourceManager()->getFileID(file_beginning));
    AddDirectInclude(include_loc, new_file, include_name_as_written,
                     GlobalSourceManager()->getFileID(file_beginning));
  }

  if (IsBuiltinOrCommandLineFile(new_file))
    return;

  ProcessHeadernameDirectivesInFile(file_beginning);

  // The first non-special file entered is the main file.
  if (main_file_ == nullptr)
    main_file_ = new_file;

  if (main_file_ != nullptr &&
      BelongsToMainCompilationUnit(GetFileEntry(include_loc), new_file)) {
    VERRS(5) << "Added to main compilation unit: "
             << GetFilePath(new_file) << "\n";
    AddGlobToReportIWYUViolationsFor(GetFilePath(new_file));
  }
  if (ShouldReportIWYUViolationsFor(new_file)) {
    files_to_report_iwyu_violations_for_.insert(new_file);
  }

  // Mark is_prefix_header.
  CHECK_(new_file && "is_prefix_header is applicable to usual files only");
  IwyuFileInfo *includee_file_info = GetFromFileInfoMap(new_file);
  const FileEntry* includer_file = GetFileEntry(include_loc);
  bool is_prefix_header = false;
  if (includer_file) {
    // File included from another prefix header file is prefix header too.
    IwyuFileInfo *includer_file_info = GetFromFileInfoMap(includer_file);
    is_prefix_header = includer_file_info->is_prefix_header();
  } else {
    // Files included from command line are prefix headers, unless it's the
    // main file.
    is_prefix_header = (new_file != main_file_);
  }
  if (is_prefix_header)
    includee_file_info->set_prefix_header();
}

// Called when done with an #included file and returning to the parent file.
void IwyuPreprocessorInfo::FileChanged_ExitToFile(
      SourceLocation include_loc, const FileEntry* exiting_from) {
  ERRSYM(GetFileEntry(include_loc)) << "[ Exiting to  ] "
                                    << PrintableLoc(include_loc) << "\n";
  if (HasOpenBeginExports(exiting_from)) {
    Warn(this, begin_exports_location_stack_.top(),
         "begin_exports without an end_exports");
    begin_exports_location_stack_.pop();
  }

  if (HasOpenBeginKeep(exiting_from)) {
    Warn(this, begin_keep_location_stack_.top(),
         "begin_keep without an end_keep");
    begin_keep_location_stack_.pop();
  }
}

void IwyuPreprocessorInfo::FileChanged_RenameFile(SourceLocation new_file) {
  ERRSYM(GetFileEntry(new_file)) << "[ Renaming to ] "
                                 << PrintableLoc(new_file) << "\n";
}

void IwyuPreprocessorInfo::FileChanged_SystemHeaderPragma(SourceLocation loc) {
  ERRSYM(GetFileEntry(loc)) << "[ #pragma s_h ] "
                            << PrintableLoc(loc) << "\n";
}

//------------------------------------------------------------
// Iwyu checkers.

// Checks whether it's OK to use the given macro defined in file defined_in.
void IwyuPreprocessorInfo::ReportMacroUse(const string& name,
                                          SourceLocation usage_location,
                                          SourceLocation dfn_location) {
  // Don't report macro uses that aren't actually in a file somewhere.
  if (!dfn_location.isValid() || GetFilePath(dfn_location) == "<built-in>")
    return;
  const FileEntry* used_in = GetFileEntry(usage_location);
  if (ShouldReportIWYUViolationsFor(used_in)) {
    // ignore symbols used outside foo.{h,cc}

    // TODO(csilvers): this isn't really a symbol use -- it may be ok
    // that the symbol isn't defined.  For instance:
    //    foo.h: #define FOO
    //    bar.h: #ifdef FOO ... #else ... #endif
    //    baz.cc: #include "foo.h"
    //            #include "bar.h"
    //    bang.cc: #include "bar.h"
    // We don't want to say that bar.h 'uses' FOO, and thus needs to
    // #include foo.h -- adding that #include could break bang.cc.
    // I think the solution is to have a 'soft' use -- don't remove it
    // if it's there, but don't add it if it's not.  Or something.
    GetFromFileInfoMap(used_in)->ReportMacroUse(usage_location, dfn_location,
                                                name);
  }
  const FileEntry* defined_in = GetFileEntry(dfn_location);
  GetFromFileInfoMap(defined_in)->ReportDefinedMacroUse(used_in);
}

// As above, but get the definition location from macros_definition_loc_.
void IwyuPreprocessorInfo::FindAndReportMacroUse(const string& name,
                                                 SourceLocation loc) {
  if (const SourceLocation* dfn_loc =
          FindInMap(&macros_definition_loc_, name)) {
    ReportMacroUse(name, loc, *dfn_loc);
  }
}

//------------------------------------------------------------
// Post-processing functions (done after all source is read).

namespace {

bool IsMacroBodyDependencyDecl(const NamedDecl* decl) {
  if (!decl || decl->isImplicit() || !decl->getIdentifier())
    return false;

  if (clang::isa<clang::TypeDecl, clang::TemplateDecl, clang::FunctionDecl,
                 clang::FieldDecl, clang::IndirectFieldDecl,
                 clang::EnumConstantDecl>(decl)) {
    return true;
  }

  const auto* variable = clang::dyn_cast<clang::VarDecl>(decl);
  return variable &&
         (variable->isFileVarDecl() || variable->isStaticDataMember());
}

struct MacroLookupFact {
  const NamedDecl* identity;
  SourceLocation exposure_location;
  bool is_using_exposure;
};

struct NamespaceAliasFact {
  string alias_name;
  string target_name;
  SourceLocation exposure_location;
};

struct MacroDeclarationIndex {
  map<string, vector<MacroLookupFact>> lookups;
  map<const NamedDecl*, vector<SourceLocation>> identity_locations;
  vector<NamespaceAliasFact> namespace_aliases;
};

string AbsoluteQualifiedName(const NamedDecl* decl) {
  const string name =
      NormalizeNamespaces(GetWrittenQualifiedNameAsString(decl));
  return name.empty() ? "" : "::" + name;
}

void AddMacroLookupFact(const string& lookup_name, const NamedDecl* target,
                        SourceLocation exposure_location,
                        bool is_using_exposure,
                        MacroDeclarationIndex* index) {
  if (lookup_name.empty() || !target || !IsMacroBodyDependencyDecl(target))
    return;
  const NamedDecl* identity =
      clang::cast<NamedDecl>(target->getCanonicalDecl());
  index->lookups[lookup_name].push_back(
      {identity, exposure_location, is_using_exposure});
}

void IndexMacroBodyDeclarations(const clang::DeclContext* context,
                                MacroDeclarationIndex* index) {
  for (const clang::Decl* decl : context->decls()) {
    if (const auto* named = clang::dyn_cast<NamedDecl>(decl)) {
      if (IsMacroBodyDependencyDecl(named)) {
        const NamedDecl* identity =
            clang::cast<NamedDecl>(named->getCanonicalDecl());
        index->identity_locations[identity].push_back(GetLocation(named));
        AddMacroLookupFact(AbsoluteQualifiedName(named), named,
                           GetLocation(named), false, index);
      }
    }

    if (const auto* using_decl = clang::dyn_cast<clang::UsingDecl>(decl)) {
      const string exposed_name = AbsoluteQualifiedName(using_decl);
      for (const clang::UsingShadowDecl* shadow : using_decl->shadows()) {
        AddMacroLookupFact(exposed_name, shadow->getTargetDecl(),
                           GetLocation(using_decl), true, index);
      }
    }

    if (const auto* alias = clang::dyn_cast<clang::NamespaceAliasDecl>(decl)) {
      const string alias_name = AbsoluteQualifiedName(alias);
      const string target_name = AbsoluteQualifiedName(alias->getNamespace());
      if (!alias_name.empty() && !target_name.empty()) {
        index->namespace_aliases.push_back(
            {alias_name, target_name, GetLocation(alias)});
      }
    }

    // Function bodies contain arbitrary local names that cannot establish the
    // declaration identity of an unexpanded macro. Namespace and record
    // contexts do carry qualified declaration and using-exposure identities.
    if (clang::isa<clang::FunctionDecl>(decl))
      continue;
    if (const auto* child = clang::dyn_cast<clang::DeclContext>(decl))
      IndexMacroBodyDeclarations(child, index);
  }
}

void IndexNamespaceAliasLookups(MacroDeclarationIndex* index) {
  // NamespaceAliasDecl::getNamespace returns the ultimate namespace target, so
  // this projection also handles alias chains without parsing source text.
  const auto direct_lookups = index->lookups;
  for (const NamespaceAliasFact& alias : index->namespace_aliases) {
    for (const auto& lookup : direct_lookups) {
      if (lookup.first.size() <= alias.target_name.size() ||
          !StartsWith(lookup.first, alias.target_name + "::")) {
        continue;
      }
      const string exposed_name =
          alias.alias_name + lookup.first.substr(alias.target_name.size());
      for (const MacroLookupFact& fact : lookup.second) {
        if (fact.is_using_exposure)
          continue;
        index->lookups[exposed_name].push_back(
            {fact.identity, alias.exposure_location, true});
      }
    }
  }
}

bool FileIdContains(const clang::SourceManager& source_manager,
                    FileID ancestor, SourceLocation location) {
  location = source_manager.getSpellingLoc(location);
  if (ancestor.isInvalid() || location.isInvalid() || !location.isFileID())
    return false;

  FileID current = source_manager.getFileID(location);
  while (current.isValid()) {
    if (current == ancestor)
      return true;
    const SourceLocation include_loc = source_manager.getIncludeLoc(current);
    if (include_loc.isInvalid() || !include_loc.isFileID())
      break;
    current = source_manager.getFileID(include_loc);
  }
  return false;
}

string MacroReferenceSpelling(
    const vector<const clang::IdentifierInfo*>& components,
    bool globally_qualified) {
  string spelling = globally_qualified ? "::" : "";
  for (const clang::IdentifierInfo* component : components) {
    if (!spelling.empty() && spelling != "::")
      spelling += "::";
    spelling += component->getName().str();
  }
  return spelling;
}

bool QualifiedLookupMatches(const string& indexed_name,
                            const string& reference,
                            bool globally_qualified) {
  if (globally_qualified)
    return indexed_name == reference;
  if (indexed_name == "::" + reference)
    return true;
  return indexed_name.size() > reference.size() + 2 &&
         EndsWith(indexed_name, "::" + reference);
}

struct ResolvedMacroDependencyFact {
  string macro_name;
  SourceLocation macro_definition_location;
  SourceLocation reference_location;
  string reference;
  const NamedDecl* identity;
};

struct MacroDependencyReport {
  string macro_name;
  string reference;
  string resolved_name;
  bool unavailable = false;
  vector<ResolvedMacroDependencyFact> facts;
};

}  // namespace

void IwyuPreprocessorInfo::ResolveMacroBodyDependencies(
    clang::ASTContext& context) {
  MacroDeclarationIndex declarations;
  IndexMacroBodyDeclarations(context.getTranslationUnitDecl(), &declarations);
  IndexNamespaceAliasLookups(&declarations);

  const auto& source_manager = preprocessor_->getSourceManager();
  map<pair<const FileEntry*, const FileEntry*>, MacroDependencyReport> reports;

  for (const MacroBodyDependency& body : macro_body_dependencies_) {
    if (body.retired_in_defining_instance)
      continue;

    SourceLocation macro_loc =
        source_manager.getExpansionLoc(body.definition_location);
    const FileEntry* macro_file = GetFileEntry(macro_loc);
    if (!macro_file || macro_loc.isInvalid() || !macro_loc.isFileID())
      continue;
    const FileID macro_file_id = source_manager.getFileID(macro_loc);

    vector<const DirectIncludeCondition*> direct_occurrences;
    vector<const DirectIncludeCondition*> conditional_occurrences;
    for (const DirectIncludeCondition& include : direct_include_conditions_) {
      SourceLocation include_loc =
          source_manager.getExpansionLoc(include.location);
      if (include.includer != macro_file || include_loc.isInvalid() ||
          !include_loc.isFileID() ||
          source_manager.getFileID(include_loc) != macro_file_id) {
        continue;
      }
      if (ConditionAtLocationDominates(include.location, macro_loc)) {
        direct_occurrences.push_back(&include);
      } else {
        conditional_occurrences.push_back(&include);
      }
    }

    const auto instance_contains_location =
        [&](const DirectIncludeCondition* root, SourceLocation location) {
          return root->includee_file_id.isValid() &&
                 FileIdContains(source_manager, root->includee_file_id,
                                location);
        };

    for (const MacroBodyReference& reference : body.references) {
      if (reference.components.empty())
        continue;
      const string spelling = MacroReferenceSpelling(
          reference.components, reference.globally_qualified);

      vector<MacroLookupFact> matching_facts;
      for (const auto& lookup : declarations.lookups) {
        if (QualifiedLookupMatches(lookup.first, spelling,
                                   reference.globally_qualified)) {
          for (const MacroLookupFact& fact : lookup.second) {
            const bool generic_record_member =
                reference.components.size() == 1 &&
                !fact.is_using_exposure &&
                fact.identity->getDeclContext()->isRecord();
            if (!generic_record_member)
              matching_facts.push_back(fact);
          }
        }
      }
      if (matching_facts.empty())
        continue;

      set<const NamedDecl*> identities;
      set<const FileEntry*> providers;
      set<const FileEntry*> uncertain_providers;
      for (const MacroLookupFact& fact : matching_facts) {
        identities.insert(fact.identity);
        const auto identity_it =
            declarations.identity_locations.find(fact.identity);
        if (identity_it == declarations.identity_locations.end())
          continue;
        const vector<SourceLocation>& identity_locations = identity_it->second;
        for (const DirectIncludeCondition* include : direct_occurrences) {
          bool exposes_lookup = !fact.is_using_exposure;
          if (fact.is_using_exposure && include->includee_file_id.isValid()) {
            exposes_lookup =
                instance_contains_location(include, fact.exposure_location);
          }

          const bool contains_declaration =
              include->includee_file_id.isValid() &&
              std::any_of(identity_locations.begin(), identity_locations.end(),
                          [&](SourceLocation location) {
                            return instance_contains_location(include, location);
                          });
          if (exposes_lookup && contains_declaration) {
            if (reference.was_macro)
              uncertain_providers.insert(include->includee);
            else
              providers.insert(include->includee);
            continue;
          }

          // If an include guard prevented this exact instance from exposing
          // its subtree, the physical graph can only establish a conservative
          // candidate. Keep the direct wrapper, but never claim unique native
          // macro provenance from this aggregate evidence.
          const FileEntry* exposure_file = GetFileEntry(fact.exposure_location);
          const bool could_expose =
              !fact.is_using_exposure ||
              (exposure_file && FileTransitivelyIncludes(
                                    include->includee, exposure_file));
          const bool could_declare = std::any_of(
              identity_locations.begin(), identity_locations.end(),
              [&](SourceLocation location) {
                const FileEntry* declaration_file = GetFileEntry(location);
                return declaration_file &&
                       FileTransitivelyIncludes(include->includee,
                                                declaration_file);
              });
          if (could_expose && could_declare)
            uncertain_providers.insert(include->includee);
        }

        for (const DirectIncludeCondition* include :
             conditional_occurrences) {
          const bool exposes_lookup =
              !fact.is_using_exposure ||
              instance_contains_location(include, fact.exposure_location);
          const bool contains_declaration = std::any_of(
              identity_locations.begin(), identity_locations.end(),
              [&](SourceLocation location) {
                return instance_contains_location(include, location);
              });
          if (exposes_lookup && contains_declaration)
            uncertain_providers.insert(include->includee);
        }
      }
      providers.insert(uncertain_providers.begin(), uncertain_providers.end());
      if (providers.empty())
        continue;

      const bool unique = uncertain_providers.empty() &&
                          identities.size() == 1 && providers.size() == 1;
      string resolved_name;
      const NamedDecl* resolved_identity = nullptr;
      if (unique) {
        resolved_identity = *identities.begin();
        resolved_name = AbsoluteQualifiedName(resolved_identity);
      }
      for (const FileEntry* provider : providers) {
        const auto key = make_pair(macro_file, provider);
        const bool unavailable = !unique;
        MacroDependencyReport& report = reports[key];
        if (unavailable) {
          if (!report.unavailable) {
            report.macro_name = body.macro_name;
            report.reference = spelling;
            report.resolved_name.clear();
            report.facts.clear();
          }
          report.unavailable = true;
          continue;
        }
        if (report.unavailable)
          continue;
        if (report.facts.empty()) {
          report.macro_name = body.macro_name;
          report.reference = spelling;
          report.resolved_name = resolved_name;
        }
        report.facts.push_back(
            {body.macro_name, body.definition_location, reference.location,
             spelling, resolved_identity});
      }
    }
  }

  for (const auto& entry : reports) {
    const FileEntry* macro_file = entry.first.first;
    const FileEntry* provider = entry.first.second;
    const MacroDependencyReport& report = entry.second;
    IwyuFileInfo* macro_file_info = GetFromFileInfoMap(macro_file);
    if (!report.unavailable) {
      macro_file_info->ReportKnownDesiredFile(
          provider, "macro_body_dependency",
          "Function-like macro " + report.macro_name + " reference " +
              report.reference + " resolves uniquely to declaration " +
              report.resolved_name +
              " through this direct effective provider.");
      for (const ResolvedMacroDependencyFact& fact : report.facts) {
        macro_file_info->ReportMacroBodyDependency(
            provider, fact.macro_name, fact.macro_definition_location,
            fact.reference_location, fact.identity->getNameAsString(),
            AbsoluteQualifiedName(fact.identity), GetLocation(fact.identity));
      }
      ERRSYM(macro_file)
          << "Keep #include " << provider->getName() << " in "
          << macro_file->getName() << " because function-like macro "
          << report.macro_name << " reference " << report.reference
          << " resolves uniquely to " << report.resolved_name
          << " through that direct effective provider.\n";
    } else {
      macro_file_info->ReportKnownDesiredFile(
          provider, "macro_body_dependency_unavailable",
          "Function-like macro " + report.macro_name + " reference " +
              report.reference +
              " has ambiguous declaration or provider identity; schema-v1 "
              "provenance is unavailable, so the present candidate is "
              "retained conservatively.");
      ERRSYM(macro_file)
          << "Keep #include " << provider->getName() << " in "
          << macro_file->getName() << " because function-like macro "
          << report.macro_name << " reference " << report.reference
          << " is ambiguous; retain the candidate with unavailable "
             "provenance.\n";
    }
  }
}

// Adds of includer's includes, direct or indirect, into retval.
void IwyuPreprocessorInfo::AddAllIncludesAsFileEntries(
    const FileEntry* includer, set<const FileEntry*>* retval) const {
  set<const FileEntry*> direct_incs =
      FileInfoOrEmptyFor(includer).direct_includes_as_fileentries();
  for (const FileEntry* include : direct_incs) {
    if (ContainsKey(*retval, include))  // avoid infinite recursion
      continue;
    retval->insert(include);
    AddAllIncludesAsFileEntries(include, retval);
  }
}

void IwyuPreprocessorInfo::PopulateIntendsToProvideMap() {
  CHECK_(intends_to_provide_map_.empty() && "Should only call this fn once");
  // Figure out which of the header files we have are public.  We'll
  // map each one to a set of all private header files that map to it.
  map<const FileEntry*, set<const FileEntry*>> private_headers_behind;
  for (const auto& fileinfo : iwyu_file_info_map_) {
    const FileEntry* header = fileinfo.first;
    const vector<MappedInclude> public_headers_for_header =
        GlobalIncludePicker().GetCandidateHeadersForFilepath(
            GetFilePath(header));
    for (const MappedInclude& pub : public_headers_for_header) {
      if (const FileEntry* public_file =
          GetOrDefault(include_to_fileentry_map_, pub.quoted_include,
                       nullptr)) {
        CHECK_(ContainsKey(iwyu_file_info_map_, public_file));
        if (public_file != header)  // no credit for mapping to yourself :-)
          private_headers_behind[public_file].insert(header);
      }
    }
  }
  // Everyone gets to provide from their direct includes.  Public
  // headers gets to provide from *all* their includes.  Likewise,
  // when you bring in a public header (because it's one of your
  // direct includes), you bring in all its includes as well.
  // Basically, a public header is really an equivalence class of
  // itself and all its direct includes.
  // TODO(csilvers): use AddAssociatedHeaders() to get includes here.
  const IncludePicker& picker = GlobalIncludePicker();
  for (const auto& fileinfo : iwyu_file_info_map_) {
    const FileEntry* file = fileinfo.first;
    if (file == nullptr)
      continue;
    intends_to_provide_map_[file].insert(file);  // Everyone provides itself!
    if (picker.IsPublic(file)) {
      AddAllIncludesAsFileEntries(file, &intends_to_provide_map_[file]);
    } else {
      const set<const FileEntry*>& direct_includes =
          fileinfo.second.direct_includes_as_fileentries();
      for (const FileEntry* inc : direct_includes) {
        intends_to_provide_map_[file].insert(inc);
        if (picker.IsPublic(inc))
          AddAllIncludesAsFileEntries(inc, &intends_to_provide_map_[file]);
      }
    }
  }
  // Ugh, we can have two files with the same name, using
  // #include-next.  Merge them.
  for (const auto& fileinfo : iwyu_file_info_map_) {
    const FileEntry* file = fileinfo.first;
    // See if a round-trip to string and back ends up at a different file.
    const string quoted_include = ConvertToQuotedInclude(GetFilePath(file));
    const FileEntry* other_file =
        GetOrDefault(include_to_fileentry_map_, quoted_include, file);
    if (other_file != file) {
      InsertAllInto(intends_to_provide_map_[file],
                    &intends_to_provide_map_[other_file]);
      // TODO(csilvers): this isn't enough if there are *more* than 2
      // files with the same name.
      intends_to_provide_map_[file] = intends_to_provide_map_[other_file];
    }
  }
  // Finally, for convenience, we'll say every private header file
  // intends to provide exactly what its public header files do.
  // That way we don't always have to be mapping private headers to
  // public ones before calling this function.  Since we don't know
  // exactly what public header a private header might map to (if it
  // can map to more than one), we just union them all.
  // TODO(csilvers): this can be bad: if i1.h maps to both p1.h and
  //   p2.h, and we end up picking p1.h, and we say that i1.h intends
  //   to provide symbols from p2.h, we're promising a lie.  I think
  //   this is ok as long as IntendsToProvide means 'If when expanding
  //   a templated function or class in i1.h, you see the need for
  //   symbol Foo which isn't a template argument, don't worry about
  //   it.'  Double check whether that's true.
  for (const auto& header_map : private_headers_behind) {
    const FileEntry* public_header = header_map.first;
    for (const FileEntry* private_header : header_map.second) {
      CHECK_(ContainsKey(intends_to_provide_map_, private_header));
      InsertAllInto(intends_to_provide_map_[public_header],
                    &intends_to_provide_map_[private_header]);
    }
  }
  // Show our work, at a high enough verbosity level.
  for (const auto& header_map : intends_to_provide_map_) {
    VERRS(4) << "Intends-to-provide for " << GetFilePath(header_map.first)
             << ":\n";
    for (const FileEntry* private_header : header_map.second) {
      VERRS(4) << "   " << GetFilePath(private_header) << "\n";
    }
  }
}

void IwyuPreprocessorInfo::PopulateTransitiveIncludeMap() {
  CHECK_(transitive_include_map_.empty() && "Should only call this fn once");
  for (const auto& fileinfo : iwyu_file_info_map_) {
    const FileEntry* file = fileinfo.first;
    transitive_include_map_[file].insert(file);   // everyone includes itself!
    AddAllIncludesAsFileEntries(file, &transitive_include_map_[file]);
  }
}

//------------------------------------------------------------
// The public API.

void IwyuPreprocessorInfo::HandlePreprocessingDone() {
  CHECK_(main_file_ && "Main file should be present");
  FileChanged_ExitToFile(SourceLocation(), main_file_);

  // In some cases, macros can refer to macros in files that are
  // defined later in other files.  In those cases, we can't
  // do an iwyu check until all header files have been read.
  // (For instance, if we see '#define FOO(x) BAR(!x)', BAR doesn't
  // actually have to be defined until FOO is actually used, which
  // could be later in the preprocessing.)
  for (const Token& token : macros_called_from_macros_) {
    FindAndReportMacroUse(GetName(token), token.getLocation());
  }

  FinalizeDottedInlineExports();

  // Other post-processing steps.
  for (auto& file_info_map_entry : iwyu_file_info_map_) {
    file_info_map_entry.second.HandlePreprocessingDone();
  }
  MutableGlobalIncludePicker()->FinalizeAddedIncludes();
  FinalizeProtectedIncludes();
  PopulateIntendsToProvideMap();
  PopulateTransitiveIncludeMap();
}

bool IwyuPreprocessorInfo::BelongsToMainCompilationUnit(
    const FileEntry* includer, const FileEntry* includee) const {
  // TODO: Should probably have a CHECK_(main_file_), but this method is
  // currently sometimes called with a nullptr main_file_.
  if (!includee)
    return false;
  if (GetCanonicalName(GetFilePath(includee)) ==
      GetCanonicalName(GetFilePath(main_file_)))
    return true;
  // Heuristic: if the main compilation unit's *first* include is
  // a file with the same basename, assume that it's the 'associated'
  // .h file, even if the canonical names differ.  This catches
  // cases like 'foo/x.cc' #includes 'foo/public/x.h', or
  // 'foo/mailserver/x.cc' #includes 'foo/public/x.h'.
  // In the case of pch-in-code make this the *second* include,
  // as the PCH must always be first.
  int first_include_index = GlobalFlags().pch_in_code ? 2 : 1;
  if (includer == main_file_ &&
      ContainsKeyValue(num_includes_seen_, includer, first_include_index)) {
    if (GetCanonicalName(Basename(GetFilePath(includee))) ==
        GetCanonicalName(Basename(GetFilePath(main_file_))))
      return true;
  }
  return false;
}

const FileEntry* IwyuPreprocessorInfo::IncludeToFileEntry(
    const string quoted_include) const {
  return GetOrDefault(include_to_fileentry_map_, quoted_include, nullptr);
}

IwyuPreprocessorInfo::ActiveCondition
IwyuPreprocessorInfo::ActiveConditionAt(SourceLocation loc) const {
  vector<ActiveConditionalState> path;
  if (!ConditionPathAt(loc, &path))
    return {};
  if (path.empty()) {
    ActiveCondition result;
    result.status = ActiveCondition::Status::kNotApplicable;
    return result;
  }

  // Preserve the complete lexical ancestry for schema-v2 branch identity.
  // A controlling include guard is semantic framing, but it remains a real
  // parent in conditional_regions and cannot be dropped from an edit anchor.
  ActiveCondition result;
  result.active_branch = true;
  for (const ActiveConditionalState& branch : path) {
    result.source_region_path.push_back(branch.source_region_id);
    if (branch.activity != BranchActivity::kActive)
      result.active_branch = false;
  }
  result.source_region_id = result.source_region_path.back();

  size_t semantic_begin = 0;
  const ActiveConditionalState& outer = path.front();
  if (outer.branch_index == 0) {
    const FileEntry* file = GetFileEntry(outer.directive_location);
    if (file && file != main_file_) {
      clang::HeaderFileInfo& info =
          preprocessor_->getHeaderSearchInfo().getFileInfo(file);
      const clang::IdentifierInfo* controlling_macro =
          info.getControllingMacro(preprocessor_->getExternalSource());
      if (controlling_macro &&
          (!outer.macro ||
           (outer.is_ifndef && outer.macro == controlling_macro))) {
        semantic_begin = 1;
      }
    }
  }
  if (semantic_begin == path.size()) {
    result.status = ActiveCondition::Status::kNotApplicable;
    result.source_region_id.clear();
    result.source_region_path.clear();
    return result;
  }

  result.directive_location = path.back().directive_location;
  for (size_t index = semantic_begin; index < path.size(); ++index) {
    const ActiveConditionalState& branch = path[index];
    if (branch.expression.empty())
      return result;
    if (!result.expression.empty())
      result.expression += " && ";
    if (path.size() - semantic_begin == 1) {
      result.expression += branch.expression;
    } else {
      result.expression += "(" + branch.expression + ")";
    }
  }
  result.status = ActiveCondition::Status::kKnown;
  return result;
}

vector<IwyuPreprocessorInfo::ConditionalRegion>
IwyuPreprocessorInfo::ConditionalRegionsFor(const FileEntry* file) const {
  vector<ConditionalRegion> observed;
  const auto found = conditional_regions_by_file_.find(file);
  if (found == conditional_regions_by_file_.end())
    return observed;
  for (size_t index : found->second) {
    if (index < conditional_regions_.size())
      observed.push_back(conditional_regions_[index]);
  }
  const auto entered = entered_file_ids_.find(file);
  if (entered == entered_file_ids_.end() || entered->second.empty())
    return observed;

  const auto& source_manager = preprocessor_->getSourceManager();
  const FileID scan_file_id = entered->second.front();
  bool invalid = false;
  const StringRef buffer = source_manager.getBufferData(scan_file_id, &invalid);
  if (invalid)
    return observed;
  const SourceLocation file_start =
      source_manager.getLocForStartOfFile(scan_file_id);

  struct Directive {
    unsigned offset;
    unsigned body_begin;
    string kind;
  };
  vector<Directive> directives;
  for (const RawConditionalDirective& raw :
       ScanConditionalDirectives(buffer)) {
    const SourceLocation directive_location =
        file_start.getLocWithOffset(raw.directive_offset);
    directives.push_back(
        {raw.directive_offset, DirectiveBodyBeginOffset(directive_location),
         raw.kind});
  }

  struct RegionTemplate {
    string source_region_id;
    unsigned opening_offset;
    unsigned directive_offset;
    unsigned body_begin_offset;
    unsigned body_end_offset;
    unsigned branch_index;
    string parent_source_region_id;
    string directive_kind;
  };
  struct Group {
    unsigned opening_offset;
    unsigned branch_index;
    size_t current_region;
  };
  vector<RegionTemplate> templates;
  vector<Group> stack;
  bool malformed = false;
  for (const Directive& directive : directives) {
    if (directive.kind == "if" || directive.kind == "ifdef" ||
        directive.kind == "ifndef") {
      const string parent = stack.empty()
          ? string() : templates[stack.back().current_region].source_region_id;
      const string id = "branch-" + to_string(directive.offset) + "-0-" +
                        to_string(directive.offset);
      templates.push_back({id, directive.offset, directive.offset,
                           directive.body_begin, 0, 0, parent,
                           directive.kind});
      stack.push_back({directive.offset, 0, templates.size() - 1});
    } else if (directive.kind == "elif" || directive.kind == "elifdef" ||
               directive.kind == "elifndef" || directive.kind == "else") {
      if (stack.empty()) {
        malformed = true;
        break;
      }
      templates[stack.back().current_region].body_end_offset = directive.offset;
      ++stack.back().branch_index;
      const unsigned branch_index = stack.back().branch_index;
      const string parent = stack.size() > 1
          ? templates[stack[stack.size() - 2].current_region].source_region_id
          : string();
      const string id = "branch-" + to_string(stack.back().opening_offset) +
                        "-" + to_string(branch_index) + "-" +
                        to_string(directive.offset);
      templates.push_back({id, stack.back().opening_offset, directive.offset,
                           directive.body_begin, 0, branch_index, parent,
                           directive.kind});
      stack.back().current_region = templates.size() - 1;
    } else if (directive.kind == "endif") {
      if (stack.empty()) {
        malformed = true;
        break;
      }
      templates[stack.back().current_region].body_end_offset = directive.offset;
      stack.pop_back();
    }
  }
  if (malformed || !stack.empty() ||
      std::any_of(templates.begin(), templates.end(),
                  [](const RegionTemplate& region) {
                    return region.body_end_offset == 0;
                  })) {
    return observed;
  }

  map<pair<string, unsigned>, const ConditionalRegion*> exact;
  map<unsigned, FileID> file_ids;
  for (const FileID entered_id : entered->second)
    file_ids[entered_id.getHashValue()] = entered_id;
  for (const ConditionalRegion& region : observed) {
    exact[{region.source_region_id, region.file_id}] = &region;
  }

  vector<ConditionalRegion> result;
  for (const auto& file_id_entry : file_ids) {
    const unsigned file_id = file_id_entry.first;
    const SourceLocation instance_start =
        source_manager.getLocForStartOfFile(file_id_entry.second);
    map<string, BranchActivity> activity_by_region;
    map<unsigned, pair<bool, bool>> prior_group_activity;
    for (const RegionTemplate& region : templates) {
      BranchActivity activity = BranchActivity::kUnknown;
      LexicalScope lexical_scope = LexicalScope::kUnknown;
      string expression;
      const auto exact_it = exact.find({region.source_region_id, file_id});
      if (exact_it != exact.end()) {
        activity = exact_it->second->activity;
        lexical_scope = exact_it->second->lexical_scope;
        expression = exact_it->second->expression;
      } else {
        const auto parent = activity_by_region.find(
            region.parent_source_region_id);
        if (!region.parent_source_region_id.empty() &&
            parent != activity_by_region.end() &&
            parent->second == BranchActivity::kInactive) {
          activity = BranchActivity::kInactive;
        } else {
          const pair<bool, bool> prior =
              GetOrDefault(prior_group_activity, region.opening_offset,
                           pair<bool, bool>{false, false});
          if (prior.first)
            activity = BranchActivity::kInactive;
          else if (prior.second)
            activity = BranchActivity::kUnknown;
        }
      }
      activity_by_region[region.source_region_id] = activity;
      pair<bool, bool>& prior = prior_group_activity[region.opening_offset];
      prior.first = prior.first || activity == BranchActivity::kActive;
      prior.second = prior.second || activity == BranchActivity::kUnknown;
      const SourceLocation opening =
          instance_start.getLocWithOffset(region.opening_offset);
      const SourceLocation directive =
          instance_start.getLocWithOffset(region.directive_offset);
      result.push_back(
          {region.source_region_id, file_id, region.opening_offset,
           region.directive_offset, region.body_begin_offset,
           region.body_end_offset, true, region.branch_index,
           region.parent_source_region_id, region.directive_kind, expression,
           activity, lexical_scope, opening, directive});
    }
  }
  return result;
}

IwyuPreprocessorInfo::ActiveCondition
IwyuPreprocessorInfo::DirectIncludeConditionAt(
    const FileEntry* includer, const string& quoted_include,
    int line_number) const {
  SourceLocation match;
  for (const DirectIncludeCondition& include : direct_include_conditions_) {
    if (include.includer != includer ||
        include.quoted_include != quoted_include ||
        GetLineNumber(include.location) != line_number) {
      continue;
    }
    if (match.isValid())
      return {};
    match = include.location;
  }
  if (match.isInvalid())
    return {};
  return ActiveConditionAt(match);
}

bool IwyuPreprocessorInfo::ConditionAtLocationDominates(
    SourceLocation provider_loc, SourceLocation use_loc) const {
  vector<ActiveConditionalState> provider_path;
  vector<ActiveConditionalState> use_path;
  if (!ConditionPathAt(provider_loc, &provider_path) ||
      !ConditionPathAt(use_loc, &use_path)) {
    return false;
  }

  const auto& source_manager = preprocessor_->getSourceManager();
  provider_loc = source_manager.getExpansionLoc(provider_loc);
  use_loc = source_manager.getExpansionLoc(use_loc);
  if (source_manager.getFileID(provider_loc) !=
      source_manager.getFileID(use_loc)) {
    return false;
  }
  if (provider_path.size() > use_path.size())
    return false;
  return std::equal(provider_path.begin(), provider_path.end(),
                    use_path.begin());
}

IwyuPreprocessorInfo::DirectIncludeCoverage
IwyuPreprocessorInfo::DirectOccurrenceCoverageAt(
    const DirectIncludeCondition& include, SourceLocation use_loc) const {
  vector<ActiveConditionalState> provider_path;
  vector<ActiveConditionalState> use_path;
  if (!ConditionPathAt(include.location, &provider_path) ||
      !ConditionPathAt(use_loc, &use_path)) {
    return DirectIncludeCoverage::kUnknown;
  }

  const auto& source_manager = preprocessor_->getSourceManager();
  const SourceLocation provider_loc =
      source_manager.getExpansionLoc(include.location);
  const SourceLocation consumer_loc = source_manager.getExpansionLoc(use_loc);
  const SourceLocation spelling_loc = source_manager.getSpellingLoc(use_loc);
  if (use_loc.isMacroID() && spelling_loc.isValid() &&
      GetLocFileEntry(spelling_loc) == include.includer &&
      source_manager.getFileID(spelling_loc) !=
          source_manager.getFileID(consumer_loc)) {
    return DirectIncludeCoverage::kUnknown;
  }
  if (provider_loc.isInvalid() || consumer_loc.isInvalid() ||
      !provider_loc.isFileID() || !consumer_loc.isFileID()) {
    return DirectIncludeCoverage::kUnknown;
  }
  if (source_manager.getFileID(provider_loc) !=
          source_manager.getFileID(consumer_loc) ||
      source_manager.getFileOffset(provider_loc) >=
          source_manager.getFileOffset(consumer_loc) ||
      !ConditionAtLocationDominates(include.location, use_loc)) {
    return DirectIncludeCoverage::kDoesNotCoverUse;
  }
  return DirectIncludeCoverage::kCoversUse;
}

bool IwyuPreprocessorInfo::DirectOccurrenceIsUnconditional(
    const DirectIncludeCondition& include, bool* unknown) const {
  CHECK_(unknown);
  const ActiveCondition condition = ActiveConditionAt(include.location);
  if (condition.status == ActiveCondition::Status::kUnknown) {
    *unknown = true;
    return false;
  }
  return condition.status == ActiveCondition::Status::kNotApplicable;
}

bool IwyuPreprocessorInfo::SkippedDottedInlineOccurrenceProvides(
    const DirectIncludeCondition& include, SourceLocation declaration_loc,
    SourceLocation use_loc) const {
  if (include.includee_file_id.isValid() || !include.includer ||
      !include.includee ||
      !IsDottedInlineHeader(GetFilePath(include.includer)) ||
      !IsExactPlainCounterpart(GetFilePath(include.includer),
                               GetFilePath(include.includee)) ||
      GetFileEntry(declaration_loc) != include.includee) {
    return false;
  }
  const auto& source_manager = preprocessor_->getSourceManager();
  const SourceLocation declaration =
      source_manager.getExpansionLoc(declaration_loc);
  const SourceLocation use = source_manager.getExpansionLoc(use_loc);
  return declaration.isValid() && declaration.isFileID() && use.isValid() &&
         use.isFileID() && IsBeforeInTranslationUnit(declaration, use);
}

IwyuPreprocessorInfo::DirectIncludeCoverage
IwyuPreprocessorInfo::DirectProviderCoverageAt(
    const FileEntry* includer, const string& selected_spelling,
    const string& selected_path, SourceLocation declaration_loc,
    SourceLocation use_loc) const {
  if (!includer || selected_spelling.empty() ||
      declaration_loc.isInvalid() || use_loc.isInvalid()) {
    return DirectIncludeCoverage::kUnknown;
  }

  const auto& source_manager = preprocessor_->getSourceManager();
  const SourceLocation definition =
      source_manager.getExpansionLoc(declaration_loc);
  if (definition.isInvalid() || !definition.isFileID())
    return DirectIncludeCoverage::kUnknown;
  const FileID definition_file_id = source_manager.getFileID(definition);
  if (definition_file_id.isInvalid())
    return DirectIncludeCoverage::kUnknown;

  const string expected_path = selected_path.empty()
      ? string()
      : NormalizeFilePath(MakeAbsolutePath(selected_path));
  bool found = false;
  bool unknown = false;
  for (const DirectIncludeCondition& include : direct_include_conditions_) {
    if (include.includer != includer ||
        include.quoted_include != selected_spelling) {
      continue;
    }
    found = true;
    if (!include.includee ||
        (!expected_path.empty() &&
         NormalizeFilePath(MakeAbsolutePath(GetFilePath(include.includee))) !=
             expected_path)) {
      continue;
    }
    const DirectIncludeCoverage root_coverage =
        DirectOccurrenceCoverageAt(include, use_loc);
    if (root_coverage == DirectIncludeCoverage::kUnknown) {
      unknown = true;
      continue;
    }
    if (root_coverage != DirectIncludeCoverage::kCoversUse)
      continue;
    if (include.includee_file_id == definition_file_id ||
        SkippedDottedInlineOccurrenceProvides(
            include, declaration_loc, use_loc)) {
      return DirectIncludeCoverage::kCoversUse;
    }
    if (include.includee_file_id.isInvalid())
      continue;

    // A mapped public provider may directly and unconditionally include the
    // file containing the declaration. Bind that edge to this exact entered
    // provider instance and declaration instance. Do not infer deeper chains.
    for (const DirectIncludeCondition& nested : direct_include_conditions_) {
      const SourceLocation nested_loc =
          source_manager.getExpansionLoc(nested.location);
      if (nested.includer != include.includee || nested_loc.isInvalid() ||
          !nested_loc.isFileID() ||
          source_manager.getFileID(nested_loc) != include.includee_file_id) {
        continue;
      }
      if (DirectOccurrenceIsUnconditional(nested, &unknown) &&
          (nested.includee_file_id == definition_file_id ||
           SkippedDottedInlineOccurrenceProvides(
               nested, declaration_loc, use_loc))) {
        return DirectIncludeCoverage::kCoversUse;
      }
    }
  }
  if (!found)
    return DirectIncludeCoverage::kNoDirectOccurrence;
  return unknown ? DirectIncludeCoverage::kUnknown
                 : DirectIncludeCoverage::kDoesNotCoverUse;
}

IwyuPreprocessorInfo::DirectIncludeCoverage
IwyuPreprocessorInfo::AssociatedProviderCoverageAt(
    const FileEntry* includer, const FileEntry* associated_header,
    const string& selected_spelling, const string& selected_path,
    SourceLocation declaration_loc, SourceLocation use_loc) const {
  if (!includer || !associated_header || selected_spelling.empty() ||
      declaration_loc.isInvalid() || use_loc.isInvalid()) {
    return DirectIncludeCoverage::kUnknown;
  }

  const auto& source_manager = preprocessor_->getSourceManager();
  const SourceLocation definition =
      source_manager.getExpansionLoc(declaration_loc);
  if (definition.isInvalid() || !definition.isFileID())
    return DirectIncludeCoverage::kUnknown;
  const FileID definition_file_id = source_manager.getFileID(definition);
  if (definition_file_id.isInvalid())
    return DirectIncludeCoverage::kUnknown;

  const string expected_path = selected_path.empty()
      ? string()
      : NormalizeFilePath(MakeAbsolutePath(selected_path));
  bool found = false;
  bool unknown = false;
  for (const DirectIncludeCondition& association :
       direct_include_conditions_) {
    if (association.includer != includer ||
        association.includee != associated_header) {
      continue;
    }
    found = true;
    const DirectIncludeCoverage root_coverage =
        DirectOccurrenceCoverageAt(association, use_loc);
    if (root_coverage == DirectIncludeCoverage::kUnknown) {
      unknown = true;
      continue;
    }
    if (root_coverage != DirectIncludeCoverage::kCoversUse ||
        association.includee_file_id.isInvalid()) {
      continue;
    }
    if (association.includee_file_id == definition_file_id &&
        association.quoted_include == selected_spelling &&
        (expected_path.empty() ||
         NormalizeFilePath(
             MakeAbsolutePath(GetFilePath(associated_header))) ==
             expected_path)) {
      return DirectIncludeCoverage::kCoversUse;
    }

    // Associated headers contribute only their exact direct provider
    // occurrences. A mapped edge beyond this point would be a deeper chain.
    for (const DirectIncludeCondition& selected :
         direct_include_conditions_) {
      const SourceLocation selected_loc =
          source_manager.getExpansionLoc(selected.location);
      if (selected.includer != associated_header ||
          selected.quoted_include != selected_spelling ||
          !selected.includee || selected_loc.isInvalid() ||
          !selected_loc.isFileID() ||
          source_manager.getFileID(selected_loc) !=
              association.includee_file_id ||
          (!expected_path.empty() &&
           NormalizeFilePath(
               MakeAbsolutePath(GetFilePath(selected.includee))) !=
               expected_path)) {
        continue;
      }
      if (!DirectOccurrenceIsUnconditional(selected, &unknown))
        continue;
      if (selected.includee_file_id == definition_file_id)
        return DirectIncludeCoverage::kCoversUse;
      if (selected.includee_file_id.isInvalid() ||
          !IsDottedInlineHeader(GetFilePath(selected.includee))) {
        continue;
      }

      // Preserve the verified plain/inline green path only. This is not a
      // general second transitive edge: the nested occurrence must be the
      // selected inline header's exact plain counterpart in the same FileID.
      for (const DirectIncludeCondition& plain :
           direct_include_conditions_) {
        const SourceLocation plain_loc =
            source_manager.getExpansionLoc(plain.location);
        if (plain.includer != selected.includee || !plain.includee ||
            !IsExactPlainCounterpart(GetFilePath(selected.includee),
                                     GetFilePath(plain.includee)) ||
            plain_loc.isInvalid() || !plain_loc.isFileID() ||
            source_manager.getFileID(plain_loc) !=
                selected.includee_file_id ||
            !DirectOccurrenceIsUnconditional(plain, &unknown)) {
          continue;
        }
        if (plain.includee_file_id == definition_file_id ||
            SkippedDottedInlineOccurrenceProvides(
                plain, declaration_loc, use_loc)) {
          return DirectIncludeCoverage::kCoversUse;
        }
      }
    }
  }
  if (!found)
    return DirectIncludeCoverage::kNoDirectOccurrence;
  return unknown ? DirectIncludeCoverage::kUnknown
                 : DirectIncludeCoverage::kDoesNotCoverUse;
}

IwyuPreprocessorInfo::DefinitionProviderCoverage
IwyuPreprocessorInfo::DefinitionProviderCoverageAt(
    const FileEntry* includer, const string& selected_spelling,
    const string& selected_path, SourceLocation definition_loc,
    SourceLocation use_loc, bool allow_later_root) const {
  DefinitionProviderCoverage result;
  if (!includer || selected_spelling.empty() || definition_loc.isInvalid() ||
      use_loc.isInvalid()) {
    return result;
  }

  const auto& source_manager = preprocessor_->getSourceManager();
  SourceLocation definition = source_manager.getExpansionLoc(definition_loc);
  SourceLocation use = source_manager.getExpansionLoc(use_loc);
  if (definition.isInvalid() || use.isInvalid() || !definition.isFileID() ||
      !use.isFileID()) {
    return result;
  }
  const FileID definition_id = source_manager.getFileID(definition);
  const FileID use_id = source_manager.getFileID(use);
  if (definition_id.isInvalid() || use_id.isInvalid() ||
      source_manager.getFileEntryForID(use_id) != includer) {
    return result;
  }

  // A same-instance definition requires no include. Its source condition must
  // nevertheless dominate the concrete obligation. A later ordinary inline
  // definition is valid in this definition domain.
  if (definition_id == use_id) {
    if (!ConditionAtLocationDominates(definition, use)) {
      result.status = DirectIncludeCoverage::kDoesNotCoverUse;
      return result;
    }
    if (!allow_later_root &&
        source_manager.getFileOffset(definition) >=
            source_manager.getFileOffset(use)) {
      result.status = DirectIncludeCoverage::kDoesNotCoverUse;
      return result;
    }
    result.status = DirectIncludeCoverage::kCoversUse;
    return result;
  }

  vector<ProviderOccurrence> reverse_chain;
  FileID current = definition_id;
  bool unknown = false;
  for (unsigned depth = 0; depth < 256 && current != use_id; ++depth) {
    SourceLocation include_loc = source_manager.getIncludeLoc(current);
    include_loc = source_manager.getExpansionLoc(include_loc);
    if (include_loc.isInvalid() || !include_loc.isFileID()) {
      result.status = DirectIncludeCoverage::kDoesNotCoverUse;
      return result;
    }
    const FileID parent = source_manager.getFileID(include_loc);
    if (parent.isInvalid())
      return result;

    const DirectIncludeCondition* exact = nullptr;
    for (const DirectIncludeCondition& include : direct_include_conditions_) {
      SourceLocation observed = source_manager.getExpansionLoc(include.location);
      if (include.includee_file_id != current || observed.isInvalid() ||
          !observed.isFileID() ||
          source_manager.getFileID(observed) != parent) {
        continue;
      }
      if (exact) {
        // A FileID has one include parent. Multiple observations mean source
        // identity was not precise enough to bind an occurrence.
        return result;
      }
      exact = &include;
    }
    if (!exact || !exact->includer || !exact->includee)
      return result;
    reverse_chain.push_back(
        {exact->includer, exact->includee, exact->quoted_include,
         exact->location, parent, exact->includee_file_id});
    current = parent;
  }
  if (current != use_id || reverse_chain.empty()) {
    result.status = DirectIncludeCoverage::kDoesNotCoverUse;
    return result;
  }
  std::reverse(reverse_chain.begin(), reverse_chain.end());
  const ProviderOccurrence& root = reverse_chain.front();

  bool saw_selected_occurrence = false;
  for (const DirectIncludeCondition& include : direct_include_conditions_) {
    SourceLocation observed = source_manager.getExpansionLoc(include.location);
    if (include.includer == includer &&
        include.quoted_include == selected_spelling && observed.isValid() &&
        observed.isFileID() && source_manager.getFileID(observed) == use_id) {
      saw_selected_occurrence = true;
    }
  }

  const string expected_path = selected_path.empty()
      ? string()
      : NormalizeFilePath(MakeAbsolutePath(selected_path));
  const string root_path = NormalizeFilePath(
      MakeAbsolutePath(GetFilePath(root.includee)));
  if (root.includer != includer || root.spelling != selected_spelling ||
      (!expected_path.empty() && root_path != expected_path)) {
    result.status = saw_selected_occurrence
        ? DirectIncludeCoverage::kDoesNotCoverUse
        : DirectIncludeCoverage::kNoDirectOccurrence;
    return result;
  }

  vector<ActiveConditionalState> root_path_condition;
  vector<ActiveConditionalState> use_path_condition;
  if (!ConditionPathAt(root.location, &root_path_condition) ||
      !ConditionPathAt(use, &use_path_condition)) {
    return result;
  }
  SourceLocation root_loc = source_manager.getExpansionLoc(root.location);
  if (root_loc.isInvalid() || !root_loc.isFileID() ||
      source_manager.getFileID(root_loc) != use_id) {
    return result;
  }
  if (root_path_condition.size() > use_path_condition.size() ||
      !std::equal(root_path_condition.begin(), root_path_condition.end(),
                  use_path_condition.begin())) {
    result.status = DirectIncludeCoverage::kDoesNotCoverUse;
    return result;
  }
  if (!allow_later_root &&
      source_manager.getFileOffset(root_loc) >= source_manager.getFileOffset(use)) {
    result.status = DirectIncludeCoverage::kDoesNotCoverUse;
    return result;
  }

  for (size_t index = 1; index < reverse_chain.size(); ++index) {
    const DirectIncludeCondition* nested = nullptr;
    const ProviderOccurrence& occurrence = reverse_chain[index];
    for (const DirectIncludeCondition& include : direct_include_conditions_) {
      if (include.location == occurrence.location &&
          include.includee_file_id == occurrence.includee_file_id) {
        nested = &include;
        break;
      }
    }
    if (!nested)
      return result;
    if (!DirectOccurrenceIsUnconditional(*nested, &unknown)) {
      result.status = unknown ? DirectIncludeCoverage::kUnknown
                              : DirectIncludeCoverage::kDoesNotCoverUse;
      return result;
    }
  }

  result.status = DirectIncludeCoverage::kCoversUse;
  result.chain = std::move(reverse_chain);
  return result;
}

IwyuPreprocessorInfo::DefinitionProviderCoverage
IwyuPreprocessorInfo::AssociatedDefinitionProviderCoverageAt(
    const FileEntry* includer, const FileEntry* associated_header,
    const string& selected_spelling, const string& selected_path,
    SourceLocation definition_loc, SourceLocation use_loc,
    bool allow_later_root) const {
  DefinitionProviderCoverage result;
  if (!includer || !associated_header || selected_spelling.empty())
    return result;

  const auto& source_manager = preprocessor_->getSourceManager();
  SourceLocation definition = source_manager.getExpansionLoc(definition_loc);
  SourceLocation use = source_manager.getExpansionLoc(use_loc);
  if (definition.isInvalid() || use.isInvalid() || !definition.isFileID() ||
      !use.isFileID()) {
    return result;
  }
  const FileID definition_id = source_manager.getFileID(definition);
  const FileID use_id = source_manager.getFileID(use);
  if (definition_id.isInvalid() || use_id.isInvalid())
    return result;

  vector<ProviderOccurrence> reverse_chain;
  FileID current = definition_id;
  for (unsigned depth = 0; depth < 256 && current != use_id; ++depth) {
    SourceLocation include_loc = source_manager.getExpansionLoc(
        source_manager.getIncludeLoc(current));
    if (include_loc.isInvalid() || !include_loc.isFileID())
      return result;
    const FileID parent = source_manager.getFileID(include_loc);
    const DirectIncludeCondition* exact = nullptr;
    for (const DirectIncludeCondition& include : direct_include_conditions_) {
      SourceLocation observed = source_manager.getExpansionLoc(include.location);
      if (include.includee_file_id == current && observed.isValid() &&
          observed.isFileID() && source_manager.getFileID(observed) == parent) {
        if (exact)
          return result;
        exact = &include;
      }
    }
    if (!exact || !exact->includer || !exact->includee)
      return result;
    reverse_chain.push_back(
        {exact->includer, exact->includee, exact->quoted_include,
         exact->location, parent, exact->includee_file_id});
    current = parent;
  }
  if (current != use_id || reverse_chain.empty()) {
    result.status = DirectIncludeCoverage::kDoesNotCoverUse;
    return result;
  }
  std::reverse(reverse_chain.begin(), reverse_chain.end());
  if (reverse_chain.front().includer != includer ||
      reverse_chain.front().includee != associated_header) {
    result.status = DirectIncludeCoverage::kNoDirectOccurrence;
    return result;
  }

  vector<ActiveConditionalState> association_path;
  vector<ActiveConditionalState> use_path;
  if (!ConditionPathAt(reverse_chain.front().location, &association_path) ||
      !ConditionPathAt(use, &use_path)) {
    return result;
  }
  if (association_path.size() > use_path.size() ||
      !std::equal(association_path.begin(), association_path.end(),
                  use_path.begin())) {
    result.status = DirectIncludeCoverage::kDoesNotCoverUse;
    return result;
  }
  SourceLocation association_loc = source_manager.getExpansionLoc(
      reverse_chain.front().location);
  if (!allow_later_root &&
      source_manager.getFileOffset(association_loc) >=
          source_manager.getFileOffset(use)) {
    result.status = DirectIncludeCoverage::kDoesNotCoverUse;
    return result;
  }

  const string expected_path = selected_path.empty()
      ? string()
      : NormalizeFilePath(MakeAbsolutePath(selected_path));
  bool selected_on_chain = false;
  bool unknown = false;
  for (size_t index = 1; index < reverse_chain.size(); ++index) {
    const ProviderOccurrence& occurrence = reverse_chain[index];
    if (occurrence.spelling == selected_spelling &&
        (expected_path.empty() ||
         NormalizeFilePath(MakeAbsolutePath(GetFilePath(occurrence.includee))) ==
             expected_path)) {
      selected_on_chain = true;
    }
    const DirectIncludeCondition* nested = nullptr;
    for (const DirectIncludeCondition& include : direct_include_conditions_) {
      if (include.location == occurrence.location &&
          include.includee_file_id == occurrence.includee_file_id) {
        nested = &include;
        break;
      }
    }
    if (!nested || !DirectOccurrenceIsUnconditional(*nested, &unknown)) {
      result.status = unknown ? DirectIncludeCoverage::kUnknown
                              : DirectIncludeCoverage::kDoesNotCoverUse;
      return result;
    }
  }
  if (!selected_on_chain) {
    result.status = DirectIncludeCoverage::kNoDirectOccurrence;
    return result;
  }
  result.status = DirectIncludeCoverage::kCoversUse;
  result.chain = std::move(reverse_chain);
  return result;
}

IwyuPreprocessorInfo::DirectIncludeCoverage
IwyuPreprocessorInfo::AssociatedDeclarationCoverageAt(
    const FileEntry* includer, const FileEntry* associated_header,
    SourceLocation declaration_loc, SourceLocation use_loc) const {
  if (!includer || !associated_header || declaration_loc.isInvalid() ||
      use_loc.isInvalid()) {
    return DirectIncludeCoverage::kUnknown;
  }
  const auto& source_manager = preprocessor_->getSourceManager();
  const SourceLocation declaration =
      source_manager.getExpansionLoc(declaration_loc);
  if (declaration.isInvalid() || !declaration.isFileID())
    return DirectIncludeCoverage::kUnknown;
  const FileID declaration_file_id = source_manager.getFileID(declaration);
  if (declaration_file_id.isInvalid())
    return DirectIncludeCoverage::kUnknown;

  bool found = false;
  bool unknown = false;
  for (const DirectIncludeCondition& association :
       direct_include_conditions_) {
    if (association.includer != includer ||
        association.includee != associated_header) {
      continue;
    }
    found = true;
    const DirectIncludeCoverage coverage =
        DirectOccurrenceCoverageAt(association, use_loc);
    if (coverage == DirectIncludeCoverage::kUnknown) {
      unknown = true;
      continue;
    }
    if (coverage == DirectIncludeCoverage::kCoversUse &&
        association.includee_file_id == declaration_file_id) {
      return DirectIncludeCoverage::kCoversUse;
    }
  }
  if (!found)
    return DirectIncludeCoverage::kNoDirectOccurrence;
  return unknown ? DirectIncludeCoverage::kUnknown
                 : DirectIncludeCoverage::kDoesNotCoverUse;
}

IwyuPreprocessorInfo::DirectIncludeCoverage
IwyuPreprocessorInfo::DirectIncludeCoverageAt(
    const FileEntry* includer, const string& quoted_include,
    SourceLocation use_loc) const {
  bool found = false;
  bool unknown = false;
  for (const DirectIncludeCondition& include : direct_include_conditions_) {
    if (include.includer != includer ||
        include.quoted_include != quoted_include) {
      continue;
    }
    found = true;
    vector<ActiveConditionalState> provider_path;
    vector<ActiveConditionalState> use_path;
    if (!ConditionPathAt(include.location, &provider_path) ||
        !ConditionPathAt(use_loc, &use_path)) {
      unknown = true;
      continue;
    }
    const auto& source_manager = preprocessor_->getSourceManager();
    const SourceLocation provider_loc =
        source_manager.getExpansionLoc(include.location);
    const SourceLocation consumer_loc = source_manager.getExpansionLoc(use_loc);
    const SourceLocation spelling_loc = source_manager.getSpellingLoc(use_loc);
    if (use_loc.isMacroID() && spelling_loc.isValid() &&
        GetLocFileEntry(spelling_loc) == includer &&
        source_manager.getFileID(spelling_loc) !=
            source_manager.getFileID(consumer_loc)) {
      // A token spelled in a macro body can be expanded in a different file or
      // include instance. Neither the spelling order nor the expansion
      // condition alone proves that this direct occurrence dominates the use.
      unknown = true;
      continue;
    }
    if (provider_loc.isInvalid() || consumer_loc.isInvalid() ||
        !provider_loc.isFileID() || !consumer_loc.isFileID()) {
      unknown = true;
      continue;
    }
    if (source_manager.getFileID(provider_loc) !=
            source_manager.getFileID(consumer_loc) ||
        source_manager.getFileOffset(provider_loc) >=
            source_manager.getFileOffset(consumer_loc)) {
      continue;
    }
    if (ConditionAtLocationDominates(include.location, use_loc))
      return DirectIncludeCoverage::kCoversUse;
  }
  if (!found)
    return DirectIncludeCoverage::kNoDirectOccurrence;
  if (unknown)
    return DirectIncludeCoverage::kUnknown;
  return DirectIncludeCoverage::kDoesNotCoverUse;
}

IwyuFileInfo* IwyuPreprocessorInfo::FileInfoFor(const FileEntry* file) const {
  return const_cast<IwyuFileInfo*>(FindInMap(&iwyu_file_info_map_, file));
}

const IwyuFileInfo& IwyuPreprocessorInfo::FileInfoOrEmptyFor(
    const FileEntry* file) const {
  const IwyuFileInfo* retval = FindInMap(&iwyu_file_info_map_, file);
  if (retval)
    return *retval;

  return empty_file_info_;
}

bool IwyuPreprocessorInfo::PublicHeaderIntendsToProvide(
    const FileEntry* public_header, const FileEntry* other_file) const {
  if (const set<const FileEntry*>* provides =
          FindInMap(&intends_to_provide_map_, public_header)) {
    return ContainsKey(*provides, other_file);
  }
  return false;
}

bool IwyuPreprocessorInfo::FileTransitivelyIncludes(
    const FileEntry* includer, const FileEntry* includee) const {
  if (const set<const FileEntry*>* all_includes =
          FindInMap(&transitive_include_map_, includer)) {
    return ContainsKey(*all_includes, includee);
  }
  return false;
}

bool IwyuPreprocessorInfo::FileTransitivelyIncludes(
    const FileEntry* includer, const string& quoted_includee) const {
  if (const set<const FileEntry*>* all_includes =
          FindInMap(&transitive_include_map_, includer)) {
    for (const FileEntry* include : *all_includes) {
      if (ConvertToQuotedInclude(GetFilePath(include)) == quoted_includee)
        return true;
    }
  }
  return false;
}

bool IwyuPreprocessorInfo::FileTransitivelyIncludes(
    const string& quoted_includer, const FileEntry* includee) const {
  for (const auto& entry : transitive_include_map_) {
    if (ConvertToQuotedInclude(GetFilePath(entry.first)) == quoted_includer)
      return ContainsKey(entry.second, includee);
  }
  return false;
}

string IwyuPreprocessorInfo::ResolveIncludePath(
    const FileEntry* includer, const string& quoted_include) const {
  if (!includer || quoted_include.size() < 3)
    return {};
  const bool is_angled = quoted_include.front() == '<';
  const char closing = is_angled ? '>' : '"';
  if ((!is_angled && quoted_include.front() != '"') ||
      quoted_include.back() != closing) {
    return {};
  }

  const FileID includer_id =
      preprocessor_->getSourceManager().translateFile(includer);
  if (includer_id.isInvalid())
    return {};
  const SourceLocation includer_loc =
      preprocessor_->getSourceManager().getLocForStartOfFile(includer_id);
  const llvm::StringRef filename(quoted_include.data() + 1,
                                 quoted_include.size() - 2);
  clang::OptionalFileEntryRef resolved = preprocessor_->LookupFile(
      includer_loc, filename, is_angled, nullptr, includer, nullptr, nullptr,
      nullptr, nullptr, nullptr, nullptr,
      /*SkipCache=*/true);
  if (!resolved)
    return {};
  return NormalizeFilePath(
      MakeAbsolutePath(GetFilePath(&resolved->getFileEntry())));
}

bool IwyuPreprocessorInfo::PathIsOnIncludeAncestry(
    SourceLocation declaration_loc, const string& normalized_path) const {
  const auto& source_manager = preprocessor_->getSourceManager();
  declaration_loc = source_manager.getSpellingLoc(declaration_loc);
  if (declaration_loc.isInvalid() || !declaration_loc.isFileID())
    return false;

  const string target =
      NormalizeFilePath(MakeAbsolutePath(normalized_path));
  FileID current = source_manager.getFileID(declaration_loc);
  set<unsigned> visited;
  while (current.isValid() && visited.insert(current.getHashValue()).second) {
    const FileEntry* file = source_manager.getFileEntryForID(current);
    if (!file)
      return false;
    if (NormalizeFilePath(MakeAbsolutePath(GetFilePath(file))) == target)
      return true;
    const SourceLocation include_loc = source_manager.getIncludeLoc(current);
    if (include_loc.isInvalid())
      break;
    const SourceLocation parent_loc = source_manager.getSpellingLoc(include_loc);
    if (parent_loc.isInvalid() || !parent_loc.isFileID())
      break;
    current = source_manager.getFileID(parent_loc);
  }
  return false;
}

vector<string> IwyuPreprocessorInfo::IncludeChainForUse(
    SourceLocation use_loc, SourceLocation declaration_loc,
    const string& normalized_selected_path) const {
  const auto& source_manager = preprocessor_->getSourceManager();
  use_loc = source_manager.getExpansionLoc(use_loc);
  declaration_loc = source_manager.getSpellingLoc(declaration_loc);
  if (use_loc.isInvalid() || declaration_loc.isInvalid() ||
      !use_loc.isFileID() || !declaration_loc.isFileID()) {
    return {};
  }

  const FileID use_id = source_manager.getFileID(use_loc);
  const string selected_path = NormalizeFilePath(
      MakeAbsolutePath(normalized_selected_path));
  vector<std::pair<FileID, string>> declaration_ancestry;
  set<unsigned> visited;
  FileID current = source_manager.getFileID(declaration_loc);
  while (current.isValid() && visited.insert(current.getHashValue()).second) {
    const FileEntry* file = source_manager.getFileEntryForID(current);
    if (!file)
      return {};
    declaration_ancestry.push_back({
        current, NormalizeFilePath(MakeAbsolutePath(GetFilePath(file)))});
    if (current == use_id)
      break;
    const SourceLocation include_loc = source_manager.getIncludeLoc(current);
    if (include_loc.isInvalid())
      return {};
    const SourceLocation parent_loc = source_manager.getSpellingLoc(include_loc);
    if (parent_loc.isInvalid() || !parent_loc.isFileID())
      return {};
    current = source_manager.getFileID(parent_loc);
  }
  if (declaration_ancestry.empty() ||
      declaration_ancestry.back().first != use_id) {
    return {};
  }

  vector<string> chain;
  bool found_selected = false;
  for (auto it = declaration_ancestry.rbegin();
       it != declaration_ancestry.rend(); ++it) {
    chain.push_back(it->second);
    if (it->second == selected_path) {
      found_selected = true;
      break;
    }
  }
  return found_selected ? chain : vector<string>{};
}

bool IwyuPreprocessorInfo::IncludeIsInhibited(
    const clang::FileEntry* file, const string& other_filename) const {
  const set<string>* inhibited_includes = FindInMap(&no_include_map_, file);
  return (inhibited_includes != nullptr) &&
      ContainsKey(*inhibited_includes, other_filename);
}

bool IwyuPreprocessorInfo::ForwardDeclareIsInhibited(
    const clang::FileEntry* file, const string& qualified_symbol_name) const {
  const string normalized_symbol_name =
      NormalizeNamespaces(qualified_symbol_name);
  const set<string>* inhibited_forward_declares =
      FindInMap(&no_forward_declare_map_, file);
  return (inhibited_forward_declares != nullptr) &&
      ContainsKey(*inhibited_forward_declares, normalized_symbol_name);
}

bool IwyuPreprocessorInfo::ForwardDeclareIsMarkedKeep(
    const NamedDecl* decl) const {
  // Use end-location so that any trailing comments match only on the last line.
  SourceLocation loc = decl->getEndLoc();

  // Is the decl part of a begin_keep/end_keep block?
  const FileEntry* file = GetFileEntry(loc);
  auto keep_ranges = keep_location_ranges_.equal_range(file);
  for (auto it = keep_ranges.first; it != keep_ranges.second; ++it) {
    if (it->second.fullyContains(loc)) {
      return true;
    }
  }
  // Is the declaration itself marked with trailing comment?
  return (LineHasText(loc, "// IWYU pragma: keep") ||
          LineHasText(loc, "/* IWYU pragma: keep"));
}

bool IwyuPreprocessorInfo::ForwardDeclareIsExported(
    const NamedDecl* decl) const {
  // Use end-location so that any trailing comments match only on the last line.
  SourceLocation loc = decl->getEndLoc();

  // Is the decl part of a begin_exports/end_exports block?
  const FileEntry* file = GetFileEntry(loc);
  auto export_ranges = export_location_ranges_.equal_range(file);
  for (auto it = export_ranges.first; it != export_ranges.second; ++it) {
    if (it->second.fullyContains(loc)) {
      return true;
    }
  }
  // Is the declaration itself marked with trailing comment?
  return (LineHasText(loc, "// IWYU pragma: export") ||
          LineHasText(loc, "/* IWYU pragma: export"));
}
}  // namespace include_what_you_use
