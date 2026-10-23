//===--- iwyu_output.cc - output-emitting code for include-what-you-use ---===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#include "iwyu_output.h"

#include <algorithm>                    // for sort, find
#include <cctype>                       // for tolower
#include <cstdio>                       // for snprintf
#include <iterator>                     // for inserter
#include <map>                          // for _Rb_tree_const_iterator, etc
#include <utility>                      // for pair, make_pair, operator>
#include <vector>                       // for vector, vector<>::iterator, etc

#include "iwyu_ast_util.h"
#include "iwyu_globals.h"
#include "iwyu_include_picker.h"
#include "iwyu_lexer_utils.h"
#include "iwyu_location_util.h"
#include "iwyu_path_util.h"
#include "iwyu_preprocessor.h"
#include "iwyu_projected_use.h"
#include "iwyu_project_policy.h"
#include "iwyu_stl_util.h"
#include "iwyu_string_util.h"
#include "iwyu_verrs.h"
#include "iwyu_version.h"
#include "llvm/ADT/ArrayRef.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/ADT/StringExtras.h"
#include "llvm/Support/Casting.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/SHA256.h"
#include "llvm/Support/raw_ostream.h"
#include "clang/AST/ASTContext.h"
#include "clang/AST/Decl.h"
#include "clang/AST/DeclBase.h"
#include "clang/AST/DeclTemplate.h"
#include "clang/AST/Type.h"
#include "clang/Basic/SourceLocation.h"
#include "clang/Basic/SourceManager.h"
#include "clang/Basic/Version.h"

namespace include_what_you_use {

using clang::ClassTemplateDecl;
using clang::ClassTemplateSpecializationDecl;
using clang::CXXMethodDecl;
using clang::CXXRecordDecl;
using clang::Decl;
using clang::DeclContext;
using clang::ElaboratedTypeLoc;
using clang::EnumDecl;
using clang::FileEntry;
using clang::FunctionDecl;
using clang::NamedDecl;
using clang::NamespaceDecl;
using clang::RecordDecl;
using clang::SourceLocation;
using clang::SourceRange;
using clang::TagDecl;
using clang::TemplateDecl;
using clang::TypeAliasDecl;
using clang::TypedefDecl;
using clang::UsingDecl;
using llvm::cast;
using llvm::dyn_cast;
using llvm::errs;
using llvm::isa;
using llvm::raw_string_ostream;
using std::map;
using std::multimap;
using std::pair;
using std::sort;
using std::to_string;
using std::vector;

namespace internal {

namespace {

class OutputLine {
 public:
  OutputLine() = default;
  explicit OutputLine(const string& line)
      : line_(line) {}
  OutputLine(const string& line, const vector<string>& symbols)
      : line_(line),
        symbols_(symbols) {
    symbols_.erase(std::remove_if(symbols_.begin(), symbols_.end(),
                                  [](const string& v) { return v.empty(); }),
                   symbols_.end());
  }

  size_t line_length() const {
    return line_.size();
  }
  bool needs_alignment() const {
    return !symbols_.empty();
  }
  void add_prefix(const string& prefix) { line_ = prefix + line_; }
  string printable_line(size_t min_length, size_t max_length) const;

 private:
  string line_;                     // '#include XXX' or 'class YYY;'
  vector<string> symbols_;          // symbols used from included header
};

// Append a helpful 'why' comment to the include line, containing the symbols
// used from the header. Align nicely at lower verbosity levels.
string OutputLine::printable_line(size_t min_length, size_t max_length) const {
  // If there are no symbols to mention, return the line as-is.
  if (symbols_.empty())
    return line_;

  CHECK_(max_length > 0);
  --max_length;  // Spare a char for newline character.

  // Before first symbol, print '  // for ' and pad it so 'why' comments are
  // nicely aligned.
  string symbol_prefix = "  // for ";
  if (line_.length() < min_length)
    symbol_prefix.insert(0, min_length - line_.length(), ' ');

  string result = line_;
  for (string symbol : symbols_) {
    // At verbose levels 0-2, truncate output to max_length columns.
    if (!ShouldPrint(3)) {
      // Calculate number of chars remaining.
      size_t remaining = 0;
      size_t result_length = result.length() + symbol_prefix.length();
      if (result_length < max_length)
        remaining = max_length - result_length;

      // Ellipsize, and if we can't fit any fragment of the symbol, give up.
      symbol = Ellipsize(symbol, remaining);
      if (symbol.empty())
        break;
    }

    result += symbol_prefix;
    result += symbol;
    symbol_prefix = ", ";
  }

  return result;
}

// A map that effectively allows us to dynamic cast from a NamedDecl
// to a FakeNamedDecl. When a FakeNamedDecl is created, it will be
// inserted into the map with itself as the key (implicitly casted to
// a NamedDecl).
std::map<const clang::NamedDecl*, const FakeNamedDecl*>
g_fake_named_decl_map;

// Since dynamic casting is not an option, this method is provided to
// determine if a decl is actually a FakeNamedDecl.
const FakeNamedDecl* FakeNamedDeclIfItIsOne(const clang::NamedDecl* decl) {
  return GetOrDefault(g_fake_named_decl_map, decl, nullptr);
}

std::string PrintableUnderlyingType(const EnumDecl* enum_decl) {
  if (const clang::TypeSourceInfo* type_source_info =
          enum_decl->getIntegerTypeSourceInfo()) {
    return " : " + type_source_info->getType().getAsString(
                       enum_decl->getASTContext().getPrintingPolicy());
  }

  return std::string();
}

}  // anonymous namespace

FakeNamedDecl::FakeNamedDecl(const string& kind_name, const string& qual_name,
                             const string& decl_filepath, int decl_linenum)
    : clang::NamedDecl(clang::Decl::Record, nullptr, clang::SourceLocation(),
                       clang::DeclarationName()),
      kind_name_(kind_name),
      qual_name_(qual_name),
      decl_filepath_(decl_filepath), decl_linenum_(decl_linenum) {
  g_fake_named_decl_map[this] = this;
}

// When testing IWYU, we provide a fake object (FakeNamedDecl) that
// needs to provide its own version of NamedDecl::getKindName() and
// NamedDecl::getQualifiedNameAsString().  Unfortunately they aren't
// virtual.  Hence we define the following helpers to dispatch the
// call ourselves.

string GetKindName(const TagDecl* tag_decl) {
  const clang::NamedDecl* const named_decl = tag_decl;
  if (const FakeNamedDecl* fake = FakeNamedDeclIfItIsOne(named_decl)) {
    return fake->kind_name();
  }

  if (const auto* enum_decl = dyn_cast<EnumDecl>(tag_decl)) {
    if (enum_decl->isScoped())
      return enum_decl->isScopedUsingClassTag() ? "enum class" : "enum struct";
  }

  return tag_decl->getKindName().str();
}

string GetQualifiedNameAsString(const clang::NamedDecl* named_decl) {
  if (const FakeNamedDecl* fake = FakeNamedDeclIfItIsOne(named_decl)) {
    return fake->qual_name();
  }
  return GetWrittenQualifiedNameAsString(named_decl);
}

// Name we put in the comments next to an #include.
string GetShortNameAsString(const clang::NamedDecl* named_decl) {
  if (const FakeNamedDecl* fake = FakeNamedDeclIfItIsOne(named_decl)) {
    return fake->qual_name();
  }

  // This is modified from NamedDecl::getQualifiedNameAsString:
  // http://clang.llvm.org/doxygen/Decl_8cpp_source.html#l00742
  const DeclContext *decl_context = named_decl->getDeclContext();
  if (decl_context->isFunctionOrMethod())
    return named_decl->getNameAsString();

  vector<const DeclContext*> contexts;
  while (decl_context && isa<NamedDecl>(decl_context)) {
    contexts.push_back(decl_context);
    decl_context = decl_context->getParent();
  };

  std::string retval;
  raw_string_ostream ostream(retval);

  for (vector<const DeclContext*>::reverse_iterator it = contexts.rbegin();
       it != contexts.rend(); ++it) {
    if (const ClassTemplateSpecializationDecl* tpl_decl = DynCastFrom(*it)) {
      ostream << tpl_decl->getName() << "<>::";
    } else if (isa<NamespaceDecl>(*it)) {
      // We don't want to include namespaces in our shortname.
    } else if (const RecordDecl *record_decl = DynCastFrom(*it)) {
      if (!record_decl->getIdentifier())
        ostream << "(anonymous " << record_decl->getKindName() << ")::";
      else
        ostream << *record_decl << "::";
    } else if (const FunctionDecl *function_decl = DynCastFrom(*it)) {
      ostream << *function_decl << "::";   // could also add in '<< "()"'
    } else if (const EnumDecl* enum_decl = DynCastFrom(*it)) {
      if (enum_decl->isScoped()) {
        ostream << *(cast<NamedDecl>(*it)) << "::";
      } else {
        // Don't add a scope prefix for old-style unscoped enums.
      }
    } else {
      ostream << *(cast<NamedDecl>(*it)) << "::";
    }
  }
  // Due to the way DeclarationNameInfo::printName() is written, this
  // will show template arguments for templated constructors and
  // destructors.  Since iwyu only shows these when they're defined in
  // a -inl.h file, I'm not going to worry about it.
  if (named_decl->getDeclName())
    ostream << *named_decl;
  else
    ostream << "(anonymous)";

  return ostream.str();
}

}  // namespace internal

// Holds information about a single full or fwd-decl use of a symbol.
OneUse::OneUse(const NamedDecl* decl, SourceLocation use_loc,
               SourceLocation decl_loc, OneUse::UseKind use_kind,
               UseFlags flags, const char* comment)
    : symbol_name_(internal::GetQualifiedNameAsString(decl)),
      short_symbol_name_(internal::GetShortNameAsString(decl)),
      decl_(decl),
      decl_loc_(GetInstantiationLoc(decl_loc)),
      decl_file_(GetFileEntry(decl_loc_)),
      decl_filepath_(GetFilePath(decl_file_)),
      use_loc_(use_loc),
      use_kind_(use_kind),             // full use or fwd-declare use
      synthetic_use_kind_(kNotSynthetic),
      use_flags_(flags),
      comment_(comment ? comment : ""),
      ignore_use_(false),
      is_iwyu_violation_(false) {
}

// This constructor always creates a full use.
OneUse::OneUse(const string& symbol_name, const FileEntry* dfn_file,
               const string& dfn_filepath, SourceLocation use_loc,
               SyntheticUseKind synthetic_use_kind)
    : symbol_name_(symbol_name),
      short_symbol_name_(symbol_name),
      decl_(nullptr),
      decl_file_(dfn_file),
      decl_filepath_(dfn_filepath),
      use_loc_(use_loc),
      use_kind_(kFullUse),
      synthetic_use_kind_(synthetic_use_kind),
      use_flags_(UF_None),
      ignore_use_(false),
      is_iwyu_violation_(false) {
  // Sometimes dfn_filepath is actually a fully quoted include.  In
  // that case, we take that as an unchangable mapping that we
  // should never remove, so we make it the suggested header.
  CHECK_(!decl_filepath_.empty() && "Must pass a real filepath to OneUse");
  if (decl_filepath_[0] == '"' || decl_filepath_[0] == '<')
    suggested_header_ = decl_filepath_;
}

void OneUse::reset_decl(const clang::NamedDecl* decl) {
    CHECK_(decl_ && "Need existing decl to reset it");
    CHECK_(decl && "Need to reset decl with existing decl");
    decl_ = decl;
    decl_file_ = GetFileEntry(decl);
    decl_filepath_ = GetFilePath(decl);
}

int OneUse::UseLinenum() const {
  return GetLineNumber(use_loc_);
}

string OneUse::PrintableUseLoc() const {
  return PrintableLoc(use_loc());
}

void OneUse::SetPublicHeaders() {
  // We should never need to deal with public headers if we already know
  // who we map to.
  CHECK_(suggested_header_.empty() && "Should not need a public header here");
  const IncludePicker& picker = GlobalIncludePicker();   // short alias
  const string use_path = GetFilePath(use_loc_);
  // If the symbol has a special mapping, use it, otherwise map its file.
  vector<CandidateHeader> candidates =
      picker.GetCandidateHeaderRefsForSymbolUsedFrom(symbol_name_, use_path);
  if (candidates.empty()) {
    candidates = picker.GetCandidateHeaderRefsForFilepathIncludedFrom(
        decl_filepath(), use_path);
  }
  if (candidates.empty()) {
    candidates.push_back({ConvertToQuotedInclude(decl_filepath()),
                          NormalizeFilePath(MakeAbsolutePath(
                              decl_filepath()))});
  }
  for (const CandidateHeader& candidate : candidates) {
    public_headers_.push_back(candidate.spelling);
    public_header_paths_.push_back(candidate.path);
  }
  CHECK_(public_headers_.size() == public_header_paths_.size());
}

const vector<string>& OneUse::public_headers() {
  if (public_headers_.empty()) {
    SetPublicHeaders();
    CHECK_(!public_headers_.empty() && "Should always have at least one hdr");
  }
  return public_headers_;
}

bool OneUse::PublicHeadersContain(const string& elt) {
  // TODO(csilvers): get rid of this method.
  return ContainsValue(public_headers(), elt);
}

bool OneUse::NeedsSuggestedHeader() const {
  return (!ignore_use() && is_full_use() && suggested_header_.empty());;
}

namespace internal {

// At verbose level 7 and above, returns a printable version of
// the pointer, suitable for being emitted after AnnotatedName.
// At lower verbose levels, returns the empty string.
string PrintablePtr(const void* ptr) {
  if (ShouldPrint(7)) {
    char buffer[32];
    snprintf(buffer, sizeof(buffer), "%p ", ptr);
    return buffer;
  }
  return "";
}

// Helpers for printing a forward declaration of a tag type or
// record type template that can be put in source code.  The hierarchy
// of the Decl classes used in these helpers looks like:
//
//   NamedDecl
//   |-- NamespaceDecl
//   |-- TemplateDecl
//   `-- TypeDecl
//       `-- TagDecl           (class, struct, union, enum)
//           `-- RecordDecl    (class, struct, union)

// Given a NamedDecl that presents a (possibly template) tag
// (i.e. class, struct, union, or enum) type declaration, and the print-out
// of its (possible) template parameters and kind (e.g. "template
// <typename T> struct"), returns its forward declaration line.
string PrintForwardDeclare(const NamedDecl* decl,
                           const string& tpl_params_and_kind,
                           bool cxx17ns) {
  // We need to short-circuit the logic for testing.
  if (const FakeNamedDecl* fake = FakeNamedDeclIfItIsOne(decl)) {
    return tpl_params_and_kind + " " + fake->qual_name() + ";";
  }

  CHECK_((isa<TagDecl>(decl) || isa<TemplateDecl>(decl)) &&
         "IWYU only allows forward declaring (possibly template) tag types");

  std::string fwd_decl = std::string(decl->getName());
  if (const auto* enum_decl = dyn_cast<EnumDecl>(decl)) {
    fwd_decl += PrintableUnderlyingType(enum_decl);
  }
  fwd_decl += ";";

  bool seen_namespace = false;
  // Anonymous namespaces are not using the more concise syntax.
  bool concat_namespaces = cxx17ns && !decl->isInAnonymousNamespace();
  for (const DeclContext* ctx = decl->getDeclContext();
       ctx && isa<NamedDecl>(ctx); ctx = ctx->getParent()) {
    if (const RecordDecl* rec = DynCastFrom(ctx)) {
      fwd_decl = std::string(rec->getName()) + "::" + fwd_decl;
    } else if (const NamespaceDecl* ns = DynCastFrom(ctx)) {
      bool first = !seen_namespace;
      if (!seen_namespace) {
        seen_namespace = true;
        fwd_decl = tpl_params_and_kind + " " + fwd_decl;
      }

      if (concat_namespaces) {
        std::string ns_name = std::string(ns->getName());
        std::string prefix = ns_name;
        std::string suffix;
        if (first) {
          first = false;
          prefix = prefix + " { ";
        }
        if (ctx->getParent() && isa<NamedDecl>(ctx->getParent())) {
          prefix = "::" + prefix;
        } else {
          prefix = "namespace " + prefix;
          suffix = " }";
        }
        fwd_decl = prefix + fwd_decl + suffix;
      } else {
        std::string ns_name = ns->isAnonymousNamespace() ?
            std::string() : (std::string(ns->getName()) + " ");
        fwd_decl = "namespace " + ns_name + "{ " + fwd_decl + " }";
      }
    } else if (const FunctionDecl* fn = DynCastFrom(ctx)) {
      // A local class (class defined inside a function).
      fwd_decl = std::string(fn->getName()) + "::" + fwd_decl;
    } else {
      CHECK_UNREACHABLE_("Unexpected decoration for type");
    }
  }

  if (!seen_namespace) {
    fwd_decl = tpl_params_and_kind + " " + fwd_decl;
  }
  return fwd_decl;
}

// Given a TagDecl, return the line that could be put in source
// code to forward-declare the type, e.g. "namespace ns { class Foo; }".
string MungedForwardDeclareLineForNontemplates(const TagDecl* decl) {
  return PrintForwardDeclare(decl, GetKindName(decl), GlobalFlags().cxx17ns);
}

// Given a TemplateDecl representing a class|struct|union template
// declaration, return the line that could be put in source code to
// forward-declare the template, e.g.
//     "namespace ns { template <typename T> class Foo; }".
string MungedForwardDeclareLineForTemplates(const TemplateDecl* decl) {
  // DeclPrinter prints the class name just as we like it (with
  // default args and everything) -- with logic that doesn't exist
  // elsewhere in clang that I can see.  Unfortunately, it also prints
  // the full class body.  So, as a hack, we use PrintableDecl to get
  // the full declaration, and then hack off everything after the
  // template name.  We also have to replace the name with the fully
  // qualified name.  TODO(csilvers): prepend namespaces instead.
  std::string line;       // llvm wants regular string, not our versa-string
  raw_string_ostream ostream(line);
  decl->print(ostream);   // calls DeclPrinter
  line = ostream.str();

  // Remove "final" specifier which isn't needed for forward
  // declarations.
  ReplaceAll(&line, " final ", " ");

  // Get rid of the superclasses, if any (this will nix the body too).
  line = Split(line, " :", 2)[0];
  // Get rid of the template body, if any (true if no superclasses).
  line = Split(line, " {", 2)[0];

  // The template name is now the last word on the line.  Replace it
  // by its fully-qualified form.
  const string::size_type name = line.rfind(' ');
  CHECK_(name != string::npos && "Unexpected printable template-type");

  line = line.substr(0, name);

  return PrintForwardDeclare(decl, line, GlobalFlags().cxx17ns);
}

string MungedForwardDeclareLine(const NamedDecl* decl) {
  if (const TagDecl* tag_decl = DynCastFrom(decl))
    return MungedForwardDeclareLineForNontemplates(tag_decl);
  else if (const TemplateDecl* template_decl = DynCastFrom(decl))
    return MungedForwardDeclareLineForTemplates(template_decl);
  CHECK_UNREACHABLE_("Unexpected decl type for MungedForwardDeclareLine");
}

static string PhysicalForwardDeclareSpelling(const NamedDecl* fwd_decl) {
  const SourceRange decl_range = GetSourceRangeOfClassDecl(fwd_decl);
  const SourceLocation begin = GetInstantiationLoc(decl_range.getBegin());
  const SourceLocation end = GetInstantiationLoc(decl_range.getEnd());
  if (begin.isInvalid() || end.isInvalid())
    return string();

  const clang::SourceManager& source_manager = *GlobalSourceManager();
  const clang::FileID begin_file = source_manager.getFileID(begin);
  if (begin_file.isInvalid() || begin_file != source_manager.getFileID(end))
    return string();

  const unsigned begin_line = source_manager.getSpellingLineNumber(begin);
  const unsigned end_line = source_manager.getSpellingLineNumber(end);
  if (begin_line == 0 || end_line < begin_line)
    return string();

  const SourceLocation physical_begin =
      source_manager.translateLineCol(begin_file, begin_line, 1);
  const SourceLocation physical_end_line =
      source_manager.translateLineCol(begin_file, end_line, 1);
  if (physical_begin.isInvalid() || physical_end_line.isInvalid())
    return string();

  bool invalid = false;
  const char* begin_data = source_manager.getCharacterData(physical_begin,
                                                            &invalid);
  if (invalid || !begin_data)
    return string();
  const char* end_line_data = source_manager.getCharacterData(
      physical_end_line, &invalid);
  if (invalid || !end_line_data || end_line_data < begin_data)
    return string();
  const llvm::StringRef final_line = GetSourceTextUntilEndOfLine(
      physical_end_line, DefaultDataGetter());
  return string(begin_data,
                static_cast<size_t>(end_line_data - begin_data) +
                    final_line.size());
}
}  // namespace internal

OneIncludeOrForwardDeclareLine::OneIncludeOrForwardDeclareLine(
    const NamedDecl* fwd_decl)
    : line_(internal::MungedForwardDeclareLine(fwd_decl)),
      source_spelling_(internal::PhysicalForwardDeclareSpelling(fwd_decl)),
      fwd_decl_(fwd_decl) {
  const SourceRange decl_lines = GetSourceRangeOfClassDecl(fwd_decl);
  // We always want to use the instantiation line numbers: for code like
  //     FORWARD_DECLARE_CLASS(MyClass);
  // we care about where this macro is called, not where it's defined.
  start_linenum_ = GetLineNumber(GetInstantiationLoc(decl_lines.getBegin()));
  end_linenum_ = GetLineNumber(GetInstantiationLoc(decl_lines.getEnd()));
}

OneIncludeOrForwardDeclareLine::OneIncludeOrForwardDeclareLine(
    ElaboratedTypeLoc type_loc)
    : is_desired_(true),
      is_present_(true),
      is_elaborated_type_(true),
      fwd_decl_(type_loc.getTypePtr()->getOwnedTagDecl()) {
  const SourceRange decl_lines = type_loc.getLocalSourceRange();
  start_linenum_ = GetLineNumber(GetInstantiationLoc(decl_lines.getBegin()));
  end_linenum_ = GetLineNumber(GetInstantiationLoc(decl_lines.getEnd()));
}

OneIncludeOrForwardDeclareLine::OneIncludeOrForwardDeclareLine(
    const FileEntry* included_file, const string& quoted_include, int linenum,
    const string& source_spelling)
    : line_("#include " + quoted_include),
      source_spelling_(source_spelling),
      start_linenum_(linenum),
      end_linenum_(linenum),
      quoted_include_(quoted_include),
      included_file_(included_file) {
}

bool OneIncludeOrForwardDeclareLine::HasSymbolUse(const string& symbol_name)
    const {
  return ContainsKey(symbol_counts_, symbol_name);
}

void OneIncludeOrForwardDeclareLine::AddSymbolUse(const string& symbol_name) {
  ++symbol_counts_[symbol_name];
}

bool OneIncludeOrForwardDeclareLine::IsIncludeLine() const {
  // Since we construct line_, we know it's in canonical form, and
  // can't look like '  #   include   <foo.h>' or some such.
  return StartsWith(line_, "#include");
}

string OneIncludeOrForwardDeclareLine::LineNumberString() const {
  char buf[64];   // big enough for any two numbers
  snprintf(buf, sizeof(buf), "%d-%d", start_linenum_, end_linenum_);
  return buf;
}

IwyuFileInfo::IwyuFileInfo(const clang::FileEntry* this_file,
                           const IwyuPreprocessorInfo* preprocessor_info,
                           const string& quoted_include_name)
  : file_(this_file),
    preprocessor_info_(preprocessor_info),
    quoted_file_(quoted_include_name),
    is_prefix_header_(false),
    is_pch_in_code_(false),
    desired_includes_have_been_calculated_(false)
{}

void IwyuFileInfo::AddAssociatedHeader(const IwyuFileInfo* other,
                                        const string& relationship,
                                        const string& reason_code,
                                        const string& reason) {
  VERRS(6) << "Adding " << GetFilePath(other->file_)
           << " as associated header for " << GetFilePath(file_)
           << " (" << reason_code << ")\n";
  associated_headers_.insert(other);
  const auto duplicate = std::find_if(
      associated_header_details_.begin(), associated_header_details_.end(),
      [&](const AssociatedHeader& association) {
        return association.file_info == other &&
               association.relationship == relationship &&
               association.reason_code == reason_code;
      });
  if (duplicate == associated_header_details_.end()) {
    associated_header_details_.push_back(
        {other, relationship, reason_code, reason});
  }
}

void IwyuFileInfo::AddInclude(const clang::FileEntry* includee,
                              const string& quoted_includee, int linenumber,
                              const string& source_spelling) {
  OneIncludeOrForwardDeclareLine new_include(
      includee, quoted_includee, linenumber, source_spelling);
  new_include.set_present();

  // It's possible for the same #include to be seen multiple times
  // (for instance, if we include a .h file twice, and that .h file
  // does not have a header guard).  Ignore all but the first.
  // TODO(csilvers): could rewrite this so it's constant-time.
  for (const OneIncludeOrForwardDeclareLine& line : lines_) {
    if (line.LineNumbersMatch(new_include)) {
      VERRS(6) << "Ignoring repeated include: "
               << GetFilePath(file_) << ":" << linenumber
               << " -> " << GetFilePath(includee) << "\n";
      return;
    }
  }

  lines_.push_back(new_include);
  // Store in a few other ways as well.
  direct_includes_as_fileentries_.insert(includee);
  direct_includes_.insert(quoted_includee);
  VERRS(6) << "Found include: "
           << GetFilePath(file_) << ":" << linenumber
           << " -> " << GetFilePath(includee) << "\n";
}

void IwyuFileInfo::AddForwardDeclare(const clang::NamedDecl* fwd_decl,
                                     const string& keep_reason_code,
                                     const string& keep_reason) {
  CHECK_(fwd_decl && "forward_declare_decl unexpectedly nullptr");
  CHECK_((isa<ClassTemplateDecl>(fwd_decl) || isa<TagDecl>(fwd_decl)) &&
         "Can only forward declare tag types and class templates");
  lines_.push_back(OneIncludeOrForwardDeclareLine(fwd_decl));
  lines_.back().set_present();
  if (!keep_reason_code.empty()) {
    lines_.back().set_desired();
    lines_.back().SetForwardDeclareKeepReason(keep_reason_code, keep_reason);
  }
  direct_forward_declares_.insert(fwd_decl);   // store in another way as well
  VERRS(6) << "Found forward-declare: "
           << GetFilePath(file_) << ":" << lines_.back().LineNumberString()
           << ": " << internal::PrintablePtr(fwd_decl)
           << internal::GetQualifiedNameAsString(fwd_decl) << "\n";
}

void IwyuFileInfo::AddElaboratedType(ElaboratedTypeLoc type_loc) {
  if (!type_loc.getTypePtr()->getOwnedTagDecl())
    return;
  lines_.push_back(OneIncludeOrForwardDeclareLine(type_loc));
  VERRS(6) << "Found owning elaborated type: " << GetFilePath(file_) << ":"
           << lines_.back().LineNumberString() << ": "
           << PrintableTypeLoc(type_loc) << "\n";
}

void IwyuFileInfo::AddUsingDecl(const UsingDecl* using_decl) {
  CHECK_(using_decl && "using_decl unexpectedly nullptr");
  using_decl_referenced_.insert(std::make_pair(using_decl, false));
  const SourceRange decl_lines = using_decl->getSourceRange();
  int start_linenum = GetLineNumber(GetInstantiationLoc(decl_lines.getBegin()));
  int end_linenum = GetLineNumber(GetInstantiationLoc(decl_lines.getEnd()));
  VERRS(6) << "Found using-decl: "
           << GetFilePath(file_) << ":"
           << to_string(start_linenum) << "-" << to_string(end_linenum) << ": "
           << internal::PrintablePtr(using_decl)
           << internal::GetQualifiedNameAsString(using_decl) << "\n";
}

static void LogSymbolUse(const string& prefix, const OneUse& use) {
  string decl_loc;
  string printable_ptr;
  if (use.decl()) {
    decl_loc = PrintableLoc(use.decl_loc());
    printable_ptr = internal::PrintablePtr(use.decl());
  } else {
    decl_loc = use.decl_filepath();
  }
  VERRS(6) << prefix << " " << printable_ptr << use.symbol_name()
           << " (from " << decl_loc << ")"
           << " at " << use.PrintableUseLoc() << "\n";
}

void IwyuFileInfo::ReportFullSymbolUse(SourceLocation use_loc,
                                       const NamedDecl* decl,
                                       UseFlags flags,
                                       const char* comment) {
  if (decl) {
    const NamedDecl* report_decl;
    SourceLocation report_decl_loc;

    if ((flags & (UF_FunctionDfn | UF_ExplicitInstantiation)) == 0) {
      // Since we need the full symbol, we need the decl's definition-site too.
      // Also, by default we canonicalize the location, using GetLocation.
      report_decl = GetDefinitionAsWritten(decl);
      report_decl_loc = GetLocation(report_decl);
    } else {
      // However, if we're defining the function or we are targeting an explicit
      // instantiation, we want to use it as-is and not try to canonicalize at all.
      report_decl = decl;
      report_decl_loc = decl->getLocation();
    }

    symbol_uses_.push_back(OneUse(report_decl, use_loc, report_decl_loc,
                                  OneUse::kFullUse, flags, comment));
    LogSymbolUse("Marked full-info use of decl", symbol_uses_.back());
  }
}

void IwyuFileInfo::ReportFullSymbolUse(SourceLocation use_loc,
                                       const FileEntry* dfn_file,
                                       const string& symbol) {
  symbol_uses_.push_back(
      OneUse(symbol, dfn_file, GetFilePath(dfn_file), use_loc));
  LogSymbolUse("Marked full-info use of symbol", symbol_uses_.back());
}

void IwyuFileInfo::ReportMacroUse(clang::SourceLocation use_loc,
                                  clang::SourceLocation dfn_loc,
                                  const string& symbol) {
  symbol_uses_.push_back(OneUse(symbol, GetFileEntry(dfn_loc),
                                GetFilePath(dfn_loc), use_loc));
  LogSymbolUse("Marked full-info use of macro", symbol_uses_.back());
}

void IwyuFileInfo::ReportDefinedMacroUse(const clang::FileEntry* used_in) {
  macro_users_.insert(used_in);
}

void IwyuFileInfo::ReportIncludeFileUse(
    const clang::FileEntry* included_file, const string& quoted_include,
    OneUse::SyntheticUseKind synthetic_use_kind) {
  symbol_uses_.push_back(OneUse("", included_file, quoted_include,
                                SourceLocation(), synthetic_use_kind));
  LogSymbolUse("Marked use of include-file", symbol_uses_.back());
}

void IwyuFileInfo::ReportKnownDesiredFile(
    const FileEntry* included_file, const string& reason_code,
    const string& reason) {
  kept_includes_.insert(included_file);
  kept_include_reasons_.insert(
      std::make_pair(included_file, KeepReason{reason_code, reason}));
}

void IwyuFileInfo::ReportProtectedFileReason(
    const FileEntry* included_file, const string& reason_code,
    const string& reason) {
  kept_include_reasons_.insert(
      std::make_pair(included_file, KeepReason{reason_code, reason}));
}

void IwyuFileInfo::ReportMacroBodyDependency(
    const FileEntry* included_file, const string& macro_name,
    SourceLocation macro_definition_location, SourceLocation reference_location,
    const string& symbol_name, const string& qualified_name,
    SourceLocation declaration_location) {
  macro_body_dependency_facts_[included_file].push_back(
      {macro_name, macro_definition_location, reference_location, symbol_name,
       qualified_name, declaration_location});
}

bool IwyuFileInfo::AddStructuredDiagnostic(SourceLocation location,
                                           const string& code,
                                           const string& message,
                                           const string& severity) {
  const auto duplicate = std::find_if(
      structured_diagnostics_.begin(), structured_diagnostics_.end(),
      [&](const StructuredDiagnostic& diagnostic) {
        return diagnostic.location == location && diagnostic.code == code &&
               diagnostic.message == message &&
               diagnostic.severity == severity;
      });
  if (duplicate != structured_diagnostics_.end())

    return false;
  structured_diagnostics_.push_back({location, code, message, severity});
  return true;
}

void IwyuFileInfo::ReportSemanticDefinitionProvider(
    SourceLocation include_location, const FileEntry* included_file,
    const FunctionDecl* function, SourceLocation use_location,
    SourceLocation definition_location, const string& selected_spelling,
    const string& selected_path, const vector<string>& chain) {
  if (!included_file || !function || include_location.isInvalid())
    return;
  const int line = GetLineNumber(include_location);
  if (line <= 0)
    return;
  semantic_definition_provider_lines_.insert(line);
  auto& facts = semantic_definition_provider_facts_[line];
  const FunctionDecl* canonical = function->getCanonicalDecl();
  auto [position, inserted] = facts.emplace(
      canonical, SemanticDefinitionProviderFact{});
  SemanticDefinitionProviderFact& fact = position->second;
  if (inserted) {
    fact.function = canonical;
    fact.include_location = include_location;
    fact.selected_spelling = selected_spelling;
    fact.selected_path = selected_path;
    fact.chain = chain;
  }
  fact.uses.emplace(
      std::make_pair(use_location.getRawEncoding(),
                     definition_location.getRawEncoding()),
      SemanticDefinitionProviderUseFact{use_location, definition_location});
}

void IwyuFileInfo::ReportForwardDeclareUse(SourceLocation use_loc,
                                           const NamedDecl* decl,
                                           UseFlags flags,
                                           const char* comment) {
  if (!decl)
    return;
  // Sometimes, a bug in clang (http://llvm.org/bugs/show_bug.cgi?id=8669)
  // combines friend decls with true forward-declare decls.  If that
  // happened here, replace the friend with a real fwd decl.
  decl = GetNonfriendClassRedecl(decl);
  symbol_uses_.push_back(OneUse(decl, use_loc, GetLocation(decl),
                                OneUse::kForwardDeclareUse, flags, comment));
  LogSymbolUse("Marked fwd-decl use of decl", symbol_uses_.back());
}

void IwyuFileInfo::ReportUsingDeclUse(SourceLocation use_loc,
                                      const UsingDecl* using_decl,
                                      UseFlags flags,
                                      const char* comment) {
  // If accessing a symbol through a using decl in the same file that contains
  // the using decl, we must mark the using decl as referenced. At the end of
  // traversing the AST, we check to see if a using decl is unreferenced and
  // add a full use of one of its shadow decls so that the source file
  // continues to compile.
  auto using_decl_status = using_decl_referenced_.find(using_decl);

  if (using_decl_status != using_decl_referenced_.end()) {
    using_decl_status->second = true;
  }

  // When a symbol is accessed through a using decl, we must report
  // that as a full use of the using decl because whatever file that
  // using decl is in is now required.
  ReportFullSymbolUse(use_loc, using_decl, flags, comment);
}

// Given a collection of symbol-uses for symbols defined in various
// files, figures out the minimal set of #includes needed to get those
// definitions.  Typically this is a trivial task: if we need the full
// information from a decl, we just have to #include the header file
// with the decl's definition.  But if that header file is a private
// decl -- e.g. <bits/stl_vector.h> -- we need to map that to a public
// decl first.  And if more than one public decl fits the bill, we
// want to pick the one that minimizes the number of new #includes
// added.  Stores its results by updating the input vector of
// OneUse's.  For convenience, returns the set of "desired" includes:
// all includes that were added to suggested_header.

static void LogIncludeMapping(const string& reason, const OneUse& use) {
  VERRS(6) << "Mapped " << use.decl_filepath() << " to "
           << use.suggested_header() << " for " << use.symbol_name()
           << " (" << reason << ")\n";
}

namespace internal {

bool DeclCanBeForwardDeclared(const Decl* decl, string* reason) {
  // Nothing inside an inline namespace can be forward-declared.
  if (IsInInlineNamespace(decl)) {
    *reason = "in inline namespace";
    return false;
  }

  if (isa<ClassTemplateDecl>(decl)) {
    // Class templates can always be forward-declared.
  } else if (const auto* tag_decl = dyn_cast<TagDecl>(decl)) {
    // Tag decls can be forward-declared unless they don't have
    // a type name to forward-declare (that includes lambdas).
    if (!tag_decl->getIdentifier()) {
      *reason = "declaration has no name";
      return false;
    }
  } else {
    // Other decl types are not forward-declarable.
    *reason = "not a record, enumeration or class template";
    return false;
  }

  return true;
}

bool DeclCanBeForwardDeclared(const Decl* decl) {
  string reason;
  return DeclCanBeForwardDeclared(decl, &reason);
}

// Helper to tell whether a forward-declare use is 'preceded' by a
// declaration inside the same file.  'Preceded' is in quotes, because
// it's actually ok if the declaration follows the use, inside a
// class.  (You can write a method using a Foo* before defining the
// nested class Foo later in the class.)
bool DeclIsVisibleToUseInSameFile(
    const Decl* decl, const OneUse& use,
    const IwyuPreprocessorInfo* preprocessor_info) {
  if (GetFileEntry(decl) != GetFileEntry(use.use_loc()))
    return false;

  // If the decl comes before the use, it's visible to it.  (The
  // decl can also be at the same location as the use, e.g. for
  //   struct Foo { int x, y; } myvar
  // )  It can even be visible if the decl comes after, if the decl
  // is inside the class definition and the use is in the body of a
  // method.
  const bool language_visible =
      IsBeforeInSameFile(decl, use.use_loc()) ||
      GetLocation(decl) == use.use_loc() ||
      (DeclsAreInSameClass(decl, use.decl()) && !decl->isOutOfLine() &&
       (use.flags() & UF_InCxxMethodBody));
  return language_visible && preprocessor_info &&
         preprocessor_info->ConditionAtLocationDominates(
             GetLocation(decl), use.use_loc());
}

// This makes a best-effort attempt to find the smallest set of
// #include files that satisfy all uses.  A more accurate name
// might be "calculate minimal-ish includes". :-)  It populates
// each OneUse in uses with the best #include for that use.
// direct_includes: this file's direct includes only.
// associated_direct_includes: direct includes for 'associated'
// files.  For everything but foo.cc, this is empty; for foo.cc it's
// foo.h's includes and foo-inl.h's includes.
set<string> CalculateMinimalIncludes(
    const string& use_quoted_include,
    const set<string>& direct_includes,
    const set<string>& associated_direct_includes,
    vector<OneUse>* uses) {
  set<string> desired_headers;
  vector<const OneUse*> sole_candidate_uses;

  // TODO(csilvers): if a use's decl supports equivalent redecls
  // (such as a FunctionDecl or TypedefDecl), pick the redecl
  // that yields the "best" #include.

  // Step (1) The easy case: decls that map to just one file.  This
  // captures both decls that aren't in private header files, and
  // those in private header files that only map to one public file.
  // For every other decl, we store the (decl, public-headers) pair.
  for (OneUse& use : *uses) {
    // We don't need to add any #includes for non-full-use.
    if (use.ignore_use() || !use.is_full_use())
      continue;
    // Special case #1: Some uses come with a suggested header already picked.
    if (use.has_suggested_header()) {
      desired_headers.insert(use.suggested_header());
      continue;
    }
    // Special case #2: if the dfn-file maps to the use-file, then
    // this is a file that the use-file is re-exporting symbols for,
    // and we should keep the #include as-is.
    const string use_file = ConvertToQuotedInclude(GetFilePath(use.use_loc()));
    const string decl_file = ConvertToQuotedInclude(use.decl_filepath());
    if (use.PublicHeadersContain(use_file) &&
        ContainsKey(direct_includes, decl_file)) {
      use.set_suggested_header(decl_file);
      desired_headers.insert(use.suggested_header());
      LogIncludeMapping("private header", use);
    } else if (use.public_headers().size() == 1) {
      use.set_suggested_header(use.public_headers()[0]);
      desired_headers.insert(use.suggested_header());
      sole_candidate_uses.push_back(&use);
      LogIncludeMapping("only candidate", use);
    }
  }

  // Consolidate a shared plain/inline provider only when a different use made
  // the verified inline endpoint unavoidable in step 1. This pass runs before
  // associated/direct priorities and does not alter any candidate list.
  vector<IncludePicker::VerifiedDottedInlineExport> consolidated_exports;
  for (OneUse& use : *uses) {
    if (!use.NeedsSuggestedHeader())
      continue;
    IncludePicker::VerifiedDottedInlineExport inline_export;
    if (!GlobalIncludePicker().GetVerifiedDottedInlineExport(
            use.public_headers(), GetFilePath(use.use_loc()),
            &inline_export)) {
      continue;
    }
    const bool inline_endpoint_committed = std::any_of(
        sole_candidate_uses.begin(), sole_candidate_uses.end(),
        [&](const OneUse* sole_use) {
          return sole_use != &use &&
                 sole_use->computed_public_headers().size() == 1 &&
                 sole_use->suggested_header() ==
                     inline_export.inline_spelling &&
                 NormalizeFilePath(MakeAbsolutePath(
                     sole_use->decl_filepath())) == inline_export.inline_path;
        });
    if (inline_endpoint_committed) {
      use.set_suggested_header(inline_export.inline_spelling);
      desired_headers.insert(use.suggested_header());
      consolidated_exports.push_back(inline_export);
      LogIncludeMapping("verified dotted-inline provider already needed", use);
    }
  }

  // Associated headers are represented by synthetic fixed uses. If the
  // consolidation above chose an associated inline endpoint, fold its exact
  // plain counterpart into that same provider instead of retaining both.
  for (OneUse& use : *uses) {
    if (!use.has_suggested_header() ||
        use.synthetic_use_kind() != OneUse::kAssociatedHeaderSynthetic) {
      continue;
    }
    for (const auto& inline_export : consolidated_exports) {
      if (use.suggested_header() == inline_export.plain_spelling &&
          ContainsKey(direct_includes, inline_export.plain_spelling) &&
          ContainsKey(direct_includes, inline_export.inline_spelling)) {
        use.set_suggested_header(inline_export.inline_spelling);
        break;
      }
    }
  }
  desired_headers.clear();
  for (const OneUse& use : *uses) {
    if (!use.ignore_use() && use.is_full_use() && use.has_suggested_header())
      desired_headers.insert(use.suggested_header());
  }

  // Steps (2): Go through the needed private-includes that map to
  // more than one public #include.  Use the following priority order:
  // - Ourselves.
  // - An include in associated_direct_includes (those are includes
  //   that are not going away, since we can't change associated
  //   files).
  // - Includes in direct_includes that are also already in
  //   desired_headers.
  // - Includes in desired_headers.
  // - Includes in direct_includes.
  // Picking in this order minimizes the number of #includes we add,
  // while allowing us to remove #includes if need be.
  for (OneUse& use : *uses) {
    if (!use.NeedsSuggestedHeader())
      continue;
    const vector<string>& public_headers = use.public_headers();
    for (const string& choice : public_headers) {
      if (use.has_suggested_header())
        break;
      if (choice == use_quoted_include) {
        use.set_suggested_header(choice);
        LogIncludeMapping("in self", use);
      }
    }
    // TODO(csilvers): write ElementInBoth() in iwyu_stl_util.h
    for (const string& choice : public_headers) {
      if (use.has_suggested_header())
        break;
      if (ContainsKey(associated_direct_includes, choice)) {
        use.set_suggested_header(choice);
        desired_headers.insert(use.suggested_header());
        LogIncludeMapping("in associated header", use);
      }
    }
    for (const string& choice : public_headers) {
      if (use.has_suggested_header())
        break;
      if (ContainsKey(direct_includes, choice) &&
          ContainsKey(desired_headers, choice)) {
        use.set_suggested_header(choice);
        desired_headers.insert(use.suggested_header());
        LogIncludeMapping("#include already present and needed", use);
      }
    }
    for (const string& choice : public_headers) {
      if (use.has_suggested_header())
        break;
      if (ContainsKey(desired_headers, choice)) {
        use.set_suggested_header(choice);
        desired_headers.insert(use.suggested_header());
        LogIncludeMapping("#include already needed", use);
      }
    }
    for (const string& choice : public_headers) {
      if (use.has_suggested_header())
        break;
      if (ContainsKey(direct_includes, choice)) {
        use.set_suggested_header(choice);
        desired_headers.insert(use.suggested_header());
        LogIncludeMapping("#include already present", use);
      }
    }
  }

  // Step (3): Now we have a set-cover problem: we need to end up with
  // a set of headers, called cover, so that for every i:
  //    intersection(cover, public_headers[i]) != empty_set
  // We do this greedily: we find the header that's listed the most
  // often.  Among those, we prefer the one that's listed first in
  // public_headers[i] the most often (each list is in approximate
  // best-fit order).  Among those, we choose arbitrarily.  We repeat
  // until we cover all sets.
  set<OneUse*> unmapped_uses;
  for (OneUse& use : *uses) {
    if (use.NeedsSuggestedHeader())
      unmapped_uses.insert(&use);
  }
  while (!unmapped_uses.empty()) {
    map<string, pair<int,int>> header_counts;   // total appearances, 1st's
    for (OneUse* use : unmapped_uses) {
      CHECK_(!use->has_suggested_header());
      const vector<string>& public_headers = use->public_headers();
      for (const string& choice : public_headers) {
        if (use->has_suggested_header())
          break;
        ++header_counts[choice].first;  // increment total count
        if (choice == use->public_headers()[0])
          ++header_counts[choice].second;  // increment first-in-list count
      }
    }
    pair<string, pair<int, int>> best = *header_counts.begin();
    for (const auto& header_count : header_counts) {
      // Use pair<>'s operator> to order for us.
      if (header_count.second > best.second)
        best = header_count;
    }
    const string hdr = best.first;
    desired_headers.insert(hdr);

    // Now go through and assign to symbols satisfied by this header.
    for (set<OneUse*>::iterator it = unmapped_uses.begin();
         it != unmapped_uses.end(); ) {
      if ((*it)->NeedsSuggestedHeader() && (*it)->PublicHeadersContain(hdr)) {
        (*it)->set_suggested_header(hdr);
        LogIncludeMapping("set cover", *(*it));
        // set<> has nice property that erasing doesn't invalidate iterators.
        unmapped_uses.erase(it++);   // because we just mapped it!
      } else {
        ++it;
      }
    }
  }
  return desired_headers;
}

// Calculating iwyu violations is a multi-step process.  The basic
// idea is we trim the existing uses to ones that might plausibly be
// iwyu violations, for both forward-declares (A) and full uses (B).
// Then we calculate the desired (end-result) set of #includes (C).
// After that we can do suggested trimming, with knowledge of all
// #includes, to reduce to full-use (D) and forward-declare uses (E)
// that are actually iwyu violations.
//
// Trimming forward-declare uses (1st pass):
// A1) If not a class or a templated class, recategorize as a full use.
// A2) If a templated class with default template params, recategorize
//     as a full use (forward-declaring in that case is too error-prone).
// A3) If a symbol in std, __gnu_cxx, or another system namespace,
//     recategorize as a full use.  This is entirely a policy
//     decision: we've decided never to forward-declare anything in
//     a system namespace, because it's best not to expose the internals
//     of system headers in user code, if possible.
// A4) If the file containing the use has a pragma inhibiting the forward
//     declaration of the symbol, change the use to a full info use in order
//     to make sure that the compiler can see some declaration of the symbol.
// A5) If declaration is nested inside a class or a function, discard this use.
//     The containing class/function is required to use the nested decl, and
//     so will force use of the containing header.
// A6) If any of the redeclarations of this declaration is in the same
//     file as the use (and before it), and is actually a definition,
//     discard the forward-declare use.
// A7) If any redeclaration is marked with IWYU pragma: export, mark as a
//     full use of this decl to keep its containing file included.
// A8) If --no_fwd_decls has been passed, recategorize as a full use.

// Trimming symbol uses (1st pass):
// B1) If declaration is nested inside a function, discard this use.
//     The function is required to use the nested decl, and so will force use of
//     the containing header.
// B2) If the definition of a full use comes after the use, change the
//     full use to a forward-declare use that points to a fwd-decl
//     that comes before the use.  (This is for cases like typedefs
//     where iwyu demands a full use but the language allows a
//     forward-declare.)
// B3) Discard symbol uses of a symbol defined in the same file it's used.
//     If the symbol is a typedef, function, or var -- every decl
//     that is re-declarable except for TagDecl -- discard if *any*
//     declaration is in the same file as the use.
// B4) Discard symbol uses for builtin symbols ('__builtin_memcmp') and
//     for operator new and operator delete (excluding placement new),
//     which are effectively built-in even though they're in <new>.
// B5) Discard symbol uses for member functions that live in the same
//     file as the class they're part of (the parent check suffices).
// B6) Sanity check: Discard 'backwards' #includes.  These are
//     #includes where we say a.h should #include b.h, but b.h is
//     already #including a.h.  This happens when iwyu attributes a
//     use to the wrong file.
// B7) In --transitive_includes_only mode, discard 'new' #includes.
//     These are #includes where we say a.h should #include b.h, but
//     a.h does not see b.h in its transitive #includes.  (Note: This
//     happens before include-picker mapping, so it's still possible to
//     see 'new' includes via a manual mapping.)
// B8) Discard uses of NamespaceDecls where the file also uses symbols
//     within that namespace. IWYU for the symbol will also satisfy IWYU
//     for the containing namespace(s).
// B1') Discard macro uses in the same file as the definition (B3 redux).
// B2') Discard macro uses that form a 'backwards' #include (B6 redux).
// B3') Discard macro uses from a 'new' #include (B7 redux).

// Determining 'desired' #includes:
// C1) Get a list of 'effective' direct includes.  For most files, it's
//     the same as the actual direct includes, but for the main .cc
//     file it also gets 'free' includes from its associated .h files.
// C2) For each symbol-use, calculate the set of public header files that
//     'provide' that symbol (e.g. <stddef.h> and <stdlib.h> for NULL).
// C3) Find the minimal 'set cover' over these sets: find a "add-minimal"
//     collection of files that has overlap with every set from (1).
//     "Add-minimal" means that the collection should have as few
//     files in it as possible *that we are not already #including*.
// C4) Sanity check: remove any .cc files from desired-includes unless
//     they're already in actual-includes.
//
// Calculate IWYU violations for forward-declares:
// D1) If the definition of the forward-declaration lives in a desired
//     include, or any redecl lives in the current file (and earlier
//     in the file), reassign decl_ to point to that redecl; if the
//     decl is not in the current file, mark the filename the decl
//     comes from.
// D2) If the definition is not in current includes, and no redecl is
//     in the current file (and earlier in the file), mark as an iwyu
//     violation.
//
// Calculate IWYU violations for full uses:
// E1) Sanity check: ignore the use if it would require adding an
//     #include of a .cc file.
// E2) Ignore use when the suggested header *is* the current file
//     (this can happen due to mappings).
// E3) If the desired include-file for this symbols is not in the
//     current includes, mark as an iwyu violation.

void ProcessForwardDeclare(OneUse* use,
                           const IwyuPreprocessorInfo* preprocessor_info) {
  CHECK_(use->decl() && "Must call ProcessForwardDeclare on a decl");
  CHECK_(!use->is_full_use() && "Must call ProcessForwardDeclare on fwd-decl");
  if (use->ignore_use())   // we're already ignoring it
    return;

  // (A1) If not suitable for forward-declaration, recategorize as a full use.
  string reason;
  if (!DeclCanBeForwardDeclared(use->decl(), &reason)) {
    VERRS(6) << "Moving " << use->symbol_name()
             << " from fwd-decl use to full use: " << reason << " ("
             << use->PrintableUseLoc() << ")\n";
    use->set_full_use();
    return;
  }
  // This is useful for the subsequent tests -- let's normalize some types.
  const TagDecl* tag_decl = DynCastFrom(use->decl());
  const ClassTemplateDecl* tpl_decl = DynCastFrom(use->decl());
  const ClassTemplateSpecializationDecl* spec_decl = DynCastFrom(use->decl());
  if (spec_decl)
    tpl_decl = spec_decl->getSpecializedTemplate();
  if (tpl_decl)
    tag_decl = tpl_decl->getTemplatedDecl();

  // (A2) If it has default template parameters, recategorize as a full use.
  // Suppress this if there's no definition for this class (so can't full-use).
  if (tpl_decl && HasDefaultTemplateParameters(tpl_decl) &&
      GetTagDefinition(tpl_decl) != nullptr) {
    VERRS(6) << "Moving " << use->symbol_name()
             << " from fwd-decl use to full use: has default template param"
             << " (" << use->PrintableUseLoc() << ")\n";
    use->set_full_use();
    // No return here: (A4) or (A5) may cause us to ignore this decl entirely.
  }

  // (A3) If it is in namespace std or a system ns, recategorize as a full use.
  // We can add new system namespaces here as needed.
  // TODO(csilvers): if someone has specialized a class in std, the
  // specialization should be treated as in user-space and
  // forward-declarable.  Check for that case.
  if (StartsWith(use->symbol_name(), "std::") ||
      StartsWith(use->symbol_name(), "__gnu_cxx::")) {
    VERRS(6) << "Moving " << use->symbol_name()
             << " from fwd-decl use to full use: in a system namespace "
             << " (" << use->PrintableUseLoc() << ")\n";
    use->set_full_use();
    // No return here: (A4) or (A5) may cause us to ignore this decl entirely.
  }

  // (A4) If the file containing the use has a pragma inhibiting the forward
  // declaration of the symbol, change the use to a full info use in order
  // to make sure that the compiler can see some declaration of the symbol.
  if (!use->is_full_use()) {
    if (preprocessor_info->ForwardDeclareIsInhibited(
            GetFileEntry(use->use_loc()), use->symbol_name())) {
      VERRS(6) << "Changing fwd-decl use of " << use->symbol_name()
               << " (" << use->PrintableUseLoc()
               << ") to a full-info use: no_forward_declare pragma\n";
      use->set_full_use();
    }
  }

  // (A5) If using a nested class or a type declared inside a function, discard
  // this use.
  if (IsNestedClass(tag_decl)) {
    // iwyu will require the full type of the parent class when it
    // recurses on the qualifier (any use of Foo::Bar requires the
    // full type of Foo).  So if we're forward-declared inside Foo,
    // the user will get that forward-declaration for free when
    // it gets the full definition of Foo.  The one exception is
    // when the use is itself inside the class, in which case it
    // sometimes needs the forward-declaration: for instance
    //    class Foo { class Nested; Nested* Fn(); class Nested { ... } };
    // This exception applies only when the use is in the same class
    // as the decl; we'll be conservative and apply it whenever
    // they're in the same file.
    if (GetFileEntry(use->use_loc()) != GetFileEntry(use->decl())) {
      VERRS(6) << "Ignoring fwd-decl use of " << use->symbol_name()
               << " (" << use->PrintableUseLoc() << "): nested class\n";
      use->set_ignore_use();
      return;
    }
  } else if (IsDeclaredInsideFunction(tag_decl)) {
    VERRS(6) << "Ignoring fwd-decl use of " << use->symbol_name() << " ("
             << use->PrintableUseLoc() << "): declared inside a "
             << "function\n";
    use->set_ignore_use();
    return;
  }

  // (A6) If a definition exists earlier in this file, discard this use.
  // Note: for the 'earlier' checks, what matters is the *instantiation*
  // location.
  const set<const NamedDecl*> redecls = GetTagRedecls(tag_decl);
  for (const NamedDecl* redecl : redecls) {
    CHECK_(isa<TagDecl>(redecl) && "GetTagRedecls has redecls of wrong type");
    const SourceLocation defined_loc = GetLocation(redecl);
    if (cast<TagDecl>(redecl)->isCompleteDefinition() &&
        DeclIsVisibleToUseInSameFile(redecl, *use, preprocessor_info)) {
      VERRS(6) << "Ignoring fwd-decl use of " << use->symbol_name() << " ("
               << use->PrintableUseLoc()
               << "): dfn is present: " << PrintableLoc(defined_loc) << "\n";
      use->set_ignore_use();
      return;
    }
  }

  // (A7) If any arbitrary redeclaration is marked with IWYU pragma: export,
  // reset use as a full use of this decl to keep its containing file included.
  if (!use->is_full_use()) {
    for (const Decl* redecl : use->decl()->redecls()) {
      const auto* decl = cast<NamedDecl>(redecl);
      if (preprocessor_info->ForwardDeclareIsExported(decl)) {
        use->reset_decl(decl);
        use->set_full_use();
        break;
      }
    }
  }

  // (A8) If --no_fwd_decls has been passed, and a decl can be found in one of
  // the headers, suggest that header, and recategorize as a full use. If we can
  // only find a decl in this file, it must be a self-sufficent decl being used,
  // so we can just let IWYU do its work, and there is no need to recategorize.
  if (!use->ignore_use() && GlobalFlags().no_fwd_decls) {
    bool promote_to_full_use = true;
    for (const Decl* decl = use->decl(); decl != nullptr;
         decl = decl->getPreviousDecl()) {
      if (IsBeforeInSameFile(decl->getLocation(), use->use_loc())) {
        promote_to_full_use = false;
      } else if (IsBeforeInTranslationUnit(decl->getLocation(),
                                           use->use_loc())) {
        // TODO: Choose a redecl that is already provided by a desired include, so we
        // don't keep another include that is not necessary.
        use->reset_decl(cast<NamedDecl>(decl));
        promote_to_full_use = true;
        break;
      }
    }

    if (promote_to_full_use) {
      use->set_full_use();
    }
  }
}

// Returns true if the given symbol has a mapping defined to a file.
static bool HasMapping(const string& symbol) {
  return !GlobalIncludePicker().GetCandidateHeadersForSymbol(symbol).empty();
}

void ProcessFullUse(OneUse* use, const IwyuPreprocessorInfo* preprocessor_info,
                    const vector<OneUse>& all_uses) {
  CHECK_(use->decl() && "Must call ProcessFullUse on a decl");
  CHECK_(use->is_full_use() && "Must not call ProcessFullUse on fwd-decl");
  if (use->ignore_use())   // we're already ignoring it
    return;

  // (B1) If declaration is inside a function, it can only be seen via said
  // function. Discard this use and assume the use of the function provides.
  if (IsDeclaredInsideFunction(use->decl())) {
    VERRS(6) << "Ignoring full use of " << use->symbol_name() << " ("
             << use->PrintableUseLoc() << "): declared inside a "
             << "function\n";
    use->set_ignore_use();
    return;
  }

  // We normally ignore uses for builtins, but when there is a mapping defined
  // for the symbol, we should respect that.  So, we need to determine whether
  // the symbol has any mappings.
  bool is_builtin_function = IsBuiltinFunction(use->decl());

  bool is_builtin_function_with_mappings =
      is_builtin_function && HasMapping(use->symbol_name());

  // (B2) If the definition is after the use, re-point to a prior decl.
  // If iwyu followed the language precisely, this wouldn't be
  // necessary: code wouldn't compile if a full-use didn't have the
  // definition handy yet.  But in fact, iwyu sometimes requires a full
  // type when the language doesn't, notably with typedefs.  For code
  // like 'struct f; typedef f g; struct f {};', iwyu will say the
  // typedef requires a definition of f, and as a result will say the
  // forward-decl is unnecessary (who cares about forward-decls when
  // we need a definition?), when in fact it's crucial.
  // For now, we assume a 'later' usage must be in the same file.
  if (GetFileEntry(use->use_loc()) == GetFileEntry(use->decl()) &&
      !DeclIsVisibleToUseInSameFile(
          use->decl(), *use, preprocessor_info) &&
      DeclCanBeForwardDeclared(use->decl())) {
    if (preprocessor_info->ForwardDeclareIsInhibited(
            GetFileEntry(use->use_loc()), use->symbol_name())) {
      // There is no include we could recommend for any full use, so just
      // ignore the use.
      VERRS(6) << "Ignoring use of " << use->symbol_name()
               << ": definition found later in file"
               << " and no_forward_declare pragma present("
               << use->PrintableUseLoc() << ")\n";
      use->set_ignore_use();
      return;
    }
    // Just change us to a forward-declare use.  Later, we'll decide
    // which forward-declare is the best one to keep.
    VERRS(6) << "Moving " << use->symbol_name()
             << " from full use to fwd-decl: definition found later in file"
             << " (" << use->PrintableUseLoc() << ")\n";
    use->set_forward_declare_use();
    return;
  }

  // (B3) Discard symbol uses of a symbol defined in the same file it's used.
  // If the symbol can be declared in multiple places, we count it if
  // *any* declaration is in the same file, unless the symbol is a
  // class or enum.  (Every other kind of redeclarable symbol, such as
  // functions, have the property that a decl is the same as a
  // definition from iwyu's point of view.)  We don't bother with
  // RedeclarableTemplate<> types (FunctionTemplateDecl), since for
  // those types, iwyu *does* care about the definition vs declaration.
  // All this is moot when FunctionDecls are being defined, all their redecls
  // are separately registered as uses so that a definition anchors all its
  // declarations.
  if (!(use->flags() & UF_FunctionDfn) && !is_builtin_function_with_mappings) {
    set<const NamedDecl*> all_redecls;
    if (isa<TagDecl>(use->decl()) || isa<ClassTemplateDecl>(use->decl()))
      all_redecls.insert(use->decl());  // for classes, just consider the dfn
    else
      all_redecls = GetNonTagRedecls(use->decl());
    for (const NamedDecl* redecl : all_redecls) {
      if (DeclIsVisibleToUseInSameFile(redecl, *use, preprocessor_info)) {
        VERRS(6) << "Ignoring use of " << use->symbol_name() << " ("
                 << use->PrintableUseLoc() << "): definition is present: "
                 << PrintableLoc(use->decl_loc()) << "\n";
        use->set_ignore_use();
        return;
      }
    }
  }

  // (B4) Discard symbol uses for builtin symbols, including new/delete and
  // template builtins.
  if (isa<clang::BuiltinTemplateDecl>(use->decl())) {
    VERRS(6) << "Ignoring use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << "): built-in template\n";
    use->set_ignore_use();
    return;
  }
  // A compiler builtin without a predefined header file (e.g. __builtin_..)
  if (is_builtin_function && !is_builtin_function_with_mappings) {
    VERRS(6) << "Ignoring use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << "): built-in function\n";
    use->set_ignore_use();
    return;
  }
  // Special case for operators new/delete: Only treated as built-in if they
  // are the default, non-placement versions. This is modelled in Clang as
  // 'replaceable global allocation functions': the helper method returns true
  // for anything but placement-new. Users of the 'std::nothrow' and
  // 'std::align_val_t' overloads already need to spell these two symbols, so
  // <new> will be required for them without us doing any magic for operator new
  // itself.
  if (const FunctionDecl* fn_decl = DynCastFrom(use->decl())) {
    if (fn_decl->isReplaceableGlobalAllocationFunction()) {
      VERRS(6) << "Ignoring use of " << use->symbol_name()
               << " (" << use->PrintableUseLoc() << "): built-in new/delete\n";
      use->set_ignore_use();
      return;
    }
  }

  // (B5) Discard symbol uses for class members in the same file as parent.
  if (const CXXRecordDecl* parent_decl =
          DynCastFrom(use->decl()->getDeclContext())) {
    // See if we also recorded a use of the parent.
    const NamedDecl* parent_dfn = GetDefinitionAsWritten(parent_decl);

    const FileEntry* decl_file_entry = GetFileEntry(use->decl_loc());
    const FileEntry* parent_file_entry =
        GetFileEntry(GetInstantiationLoc(GetLocation(parent_dfn)));

    // We want to map the definition-files to their public headers if
    // they're private headers (so bits/stl_vector.h and
    // bits/vector.tcc are counted as the "same" file for this test).
    // To be safe, we only do the mapping if both files have at most
    // one public file they map to (otherwise we don't know which
    // mapping to choose, and it's important we use the one that iwyu
    // will pick later).  TODO(csilvers): figure out that case too.
    const IncludePicker& picker = GlobalIncludePicker();
    const vector<MappedInclude>& member_dfn_files =
        picker.GetCandidateHeadersForFilepath(GetFilePath(decl_file_entry));
    const vector<MappedInclude>& parent_dfn_files =
        picker.GetCandidateHeadersForFilepath(GetFilePath(parent_file_entry));
    bool same_file;
    if (member_dfn_files.size() == 1 && parent_dfn_files.size() == 1) {
      same_file = (member_dfn_files[0].quoted_include ==
                   parent_dfn_files[0].quoted_include);
    } else {
      // Fall back on just checking the filenames: can't figure out public.
      same_file = (decl_file_entry == parent_file_entry);
    }
    if (same_file) {
      VERRS(6) << "Ignoring use of " << use->symbol_name()
               << " (" << use->PrintableUseLoc() << "): member of class\n";
      use->set_ignore_use();
      return;
    }
  }

  // (B6) Discard uses of symbols that form a 'backwards' #include.
  // This means that we say a.h is using a symbol in b.h, but b.h
  // already #includes a.h (either directly or indirectly).  Since the
  // include graph should be acyclic, this means that iwyu messed up,
  // either by incorrectly saying it was a.h that is using the symbol
  // (this can happen trying to figure out who 'owns' macro code), or
  // by incorrectly saying it was a use (this can happen with typedefs
  // -- we say the underlying type is 'used' in a different way than
  // the language requires).
  // TODO(csilvers): remove this when we resolve the bugs with macros/typedefs.
  if (preprocessor_info->FileTransitivelyIncludes(
          GetFileEntry(use->decl()), GetFileEntry(use->use_loc())) &&
      !is_builtin_function_with_mappings) {
    VERRS(6) << "Ignoring use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << "): 'backwards' #include\n";
    use->set_ignore_use();
    return;
  }

  // (B7) In --transitive_includes_only mode, discard 'new' #includes.
  // In practice, if we tell a.h to add an #include that is not in its
  // transitive includes, it's usually (but not always) an iwyu error
  // of some sort.  So we allow a flag to discard such recommendations.
  if (GlobalFlags().transitive_includes_only) {
    if (!preprocessor_info->FileTransitivelyIncludes(
            GetFileEntry(use->use_loc()), GetFileEntry(use->decl()))) {
      VERRS(6) << "Ignoring use of " << use->symbol_name()
               << " (" << use->PrintableUseLoc() << "):"
               << " non-transitive #include\n";
      use->set_ignore_use();
      return;
    }
  }

  // (B8) Discard uses of NamespaceDecls where the file also uses
  // symbols within that namespace. IWYU for the symbol will also
  // satisfy IWYU for the containing namespace(s).
  if (const auto* ns_decl = dyn_cast<NamespaceDecl>(use->decl())) {
    if (std::any_of(
            all_uses.begin(), all_uses.end(), [ns_decl](const OneUse& use) {
              const NamedDecl* decl = use.decl();
              return decl && (ns_decl != decl) && IsInNamespace(decl, ns_decl);
            })) {
      use->set_ignore_use();
      return;
    }
  }
}

void ProcessSymbolUse(OneUse* use,
                      const IwyuPreprocessorInfo* preprocessor_info) {
  if (use->ignore_use())   // we're already ignoring it
    return;

  const FileEntry* use_file = GetFileEntry(use->use_loc());
  const string quoted_decl_file = ConvertToQuotedInclude(use->decl_filepath());

  // (B1') Like (B2), discard symbol uses in the same file as their definition.
  if (GetFilePath(use->use_loc()) == use->decl_filepath()) {
    VERRS(6) << "Ignoring symbol use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << "): defined in same file\n";
    use->set_ignore_use();
    return;
  }

  // (B2') Like (B5), discard uses of symbols that create 'backwards' includes.
  // Note we suppress this check if suggested_header_ is already set:
  // that only happens with hard-coded uses, which we shouldn't second guess.
  // TODO(csilvers): like (B5), remove this when we have 'soft' uses.
  if (!use->has_suggested_header() &&
      preprocessor_info->FileTransitivelyIncludes(quoted_decl_file, use_file)) {
    VERRS(6) << "Ignoring use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << "): 'backwards' #include\n";
    use->set_ignore_use();
    return;
  }

  // (B3') Like (B6), discard uses of symbols that create 'new' includes.
  if (GlobalFlags().transitive_includes_only) {
    if (!use->has_suggested_header() &&
        !preprocessor_info->FileTransitivelyIncludes(use_file,
                                                     quoted_decl_file)) {
      VERRS(6) << "Ignoring use of " << use->symbol_name()
               << " (" << use->PrintableUseLoc() << "):"
               << " non-transitive #include\n";
      use->set_ignore_use();
      return;
    }
  }
}

void CalculateIwyuForForwardDeclareUse(
    OneUse* use,
    const set<string>& actual_includes,
    const set<string>& desired_includes,
    const set<string>& direct_desired_includes,
    const set<const FileEntry*>& associated_includes,
    const IwyuPreprocessorInfo* preprocessor_info) {
  CHECK_(!use->ignore_use() && "Trying to calculate on an ignored use");
  CHECK_(use->decl() && "CalculateIwyuForForwardDeclareUse takes a fwd-decl");
  CHECK_(!use->is_full_use() && "ForwardDeclareUse are not full uses");

  const NamedDecl* same_file_decl = nullptr;
  const TagDecl* tag_decl = DynCastFrom(use->decl());
  const ClassTemplateDecl* tpl_decl = DynCastFrom(use->decl());
  const ClassTemplateSpecializationDecl* spec_decl = DynCastFrom(use->decl());
  if (spec_decl)
    tpl_decl = spec_decl->getSpecializedTemplate();
  if (tpl_decl)
    tag_decl = tpl_decl->getTemplatedDecl();
  CHECK_(tag_decl && "Non-tag types should have been handled already");

  // If this tag type is defined in one of the desired or actual providers,
  // mark that fact. Direct, mapped, and associated edges must bind the selected
  // provider spelling and path to an exact entered FileID that dominates the
  // use. Missing, ambiguous, conditional, different-instance, and deeper facts
  // fail closed.
  bool dfn_is_in_desired_includes = false;
  bool dfn_is_in_actual_includes = false;
  string desired_provider;
  const FileEntry* use_file = GetFileEntry(use->use_loc());
  const auto associated_provider_covers_use =
      [&](const CandidateHeader& candidate, SourceLocation declaration_loc) {
        for (const FileEntry* associated : associated_includes) {
          if (preprocessor_info->AssociatedProviderCoverageAt(
                  use_file, associated, candidate.spelling, candidate.path,
                  declaration_loc, use->use_loc()) ==
              IwyuPreprocessorInfo::DirectIncludeCoverage::kCoversUse) {
            return true;
          }
        }
        return false;
      };

  const NamedDecl* dfn = GetTagDefinition(use->decl());
  if (dfn) {
    vector<CandidateHeader> headers =
        GlobalIncludePicker().GetCandidateHeaderRefsForFilepathIncludedFrom(
            GetFilePath(dfn), GetFilePath(use->use_loc()));
    const string definition_path = GetFilePath(dfn);
    const CandidateHeader physical_header{
        ConvertToQuotedInclude(definition_path), definition_path};
    if (std::none_of(headers.begin(), headers.end(),
                     [&](const CandidateHeader& header) {
                       return header.spelling == physical_header.spelling &&
                              header.path == physical_header.path;
                     })) {
      headers.push_back(physical_header);
    }
    for (const CandidateHeader& header : headers) {
      const auto direct_coverage =
          preprocessor_info->DirectProviderCoverageAt(
              use_file, header.spelling, header.path, GetLocation(dfn),
              use->use_loc());
      const bool present_provider_covers =
          direct_coverage ==
              IwyuPreprocessorInfo::DirectIncludeCoverage::kCoversUse ||
          associated_provider_covers_use(header, GetLocation(dfn));
      // Preserve established one-pass behavior for a newly selected spelling
      // of the definition's physical file. A mapped public provider has a
      // different path and must have an entered occurrence before it can cover
      // the declaration.
      const bool new_physical_provider_covers =
          direct_coverage ==
              IwyuPreprocessorInfo::DirectIncludeCoverage::
                  kNoDirectOccurrence &&
          header.path == physical_header.path &&
          ContainsKey(direct_desired_includes, header.spelling);
      if (ContainsKey(desired_includes, header.spelling) &&
          (present_provider_covers || new_physical_provider_covers)) {
        dfn_is_in_desired_includes = true;
        if (desired_provider.empty())
          desired_provider = header.spelling;
      }
      if (ContainsKey(actual_includes, header.spelling) &&
          present_provider_covers) {
        dfn_is_in_actual_includes = true;
      }
    }
    // We ourself are always a 'desired' and 'actual' include (though
    // only if the definition is visible from the use location).
    if (DeclIsVisibleToUseInSameFile(dfn, *use, preprocessor_info)) {
      dfn_is_in_desired_includes = true;
      dfn_is_in_actual_includes = true;
    }
  }

  // We also want to know if *any* redecl of this type is defined
  // in the same file as the use (and before it).
  const set<const NamedDecl*>& redecls = GetTagRedecls(tag_decl);
  for (const NamedDecl* redecl : redecls) {
    if (DeclIsVisibleToUseInSameFile(redecl, *use, preprocessor_info)) {
      same_file_decl = redecl;
      break;
    }
  }
  // If there's no redecl in the .cc file, accept one in an associated header
  // only when the exact entered associated instance dominates this use.
  if (!same_file_decl) {
    for (const NamedDecl* redecl : redecls) {
      const FileEntry* associated = GetFileEntry(redecl);
      if (!ContainsKey(associated_includes, associated))
        continue;
      if (preprocessor_info->AssociatedDeclarationCoverageAt(
              use_file, associated, GetLocation(redecl), use->use_loc()) ==
          IwyuPreprocessorInfo::DirectIncludeCoverage::kCoversUse) {
        same_file_decl = redecl;
        break;
      }
    }
  }

  // (D1) Mark that the fwd-declare is satisfied by dfn in desired include.
  const NamedDecl* providing_decl = nullptr;
  if (dfn_is_in_desired_includes) {
    providing_decl = dfn;
    VERRS(6) << "Noting fwd-decl use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << ") is satisfied by dfn in "
             << PrintableLoc(GetLocation(providing_decl)) << "\n";
    // Mark that this use is another reason we want this exact selected header.
    // Same-file definitions retain the historical fallback spelling.
    use->set_suggested_header(
        desired_provider.empty()
            ? ConvertToQuotedInclude(GetFilePath(dfn))
            : desired_provider);
  } else if (same_file_decl) {
    providing_decl = same_file_decl;
    VERRS(6) << "Noting fwd-decl use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << ") is declared at "
             << PrintableLoc(GetLocation(providing_decl)) << "\n";
    // If same_file_decl is actually in an associated .h, mark our use
    // of that.  No need to map-to-public for associated .h files.
    if (GetFileEntry(same_file_decl) != GetFileEntry(use->use_loc()))
      use->set_suggested_header(GetFilePath(same_file_decl));
  }
  if (providing_decl) {
    // Change decl_ to point to this "better" redecl.
    use->reset_decl(providing_decl);
  }

  // Be sure to store as a TemplateClassDecl if we're a templated
  // class.
  if (const ClassTemplateSpecializationDecl* spec_decl =
          DynCastFrom(use->decl())) {
    use->reset_decl(spec_decl->getSpecializedTemplate());
  } else if (const CXXRecordDecl* cxx_decl = DynCastFrom(use->decl())) {
    if (cxx_decl->getDescribedClassTemplate())
      use->reset_decl(cxx_decl->getDescribedClassTemplate());
  }

  // (D2) Mark iwyu violation unless defined in a current #include.
  if (dfn_is_in_actual_includes) {
    VERRS(6) << "Ignoring fwd-decl use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << "): have definition at "
             << PrintableLoc(GetLocation(dfn)) << "\n";
  } else if (same_file_decl) {
    VERRS(6) << "Ignoring fwd-decl use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << "): have earlier fwd-decl at "
             << PrintableLoc(GetLocation(same_file_decl)) << "\n";
  } else {
    use->set_is_iwyu_violation();
  }
}

void CalculateIwyuForFullUse(OneUse* use,
                             const set<string>& actual_includes,
                             const string& use_quoted_include) {
  CHECK_(!use->ignore_use() && "Trying to calculate on an ignored use");
  CHECK_(use->is_full_use() && "CalculateIwyuForFullUse requires a full use");
  CHECK_(use->has_suggested_header() && "All full uses must have a header");

  // (E1) Discard uses of a symbol declared in a .cc and used
  // elsewhere.  Unless that 'elsewhere' is #including the .cc file,
  // then something is wrong: we're using a symbol from a file we
  // can't possibly be #including.  There are several ways this could
  // happen:
  // (1)
  //   foo.h:  #ifdef FOO ...
  //   foo-inl.cc: #define FOO
  //   foo.cc: #include "foo-inl.cc"
  //           #include "foo.h"   // foo.h 'uses' FOO from foo-inl.cc
  // (Though this is arguably a bug in iwyu, and FOO should be treated as
  // a 'soft' use here; see comments in iwyu_preprocessor.cc:ReportMacroUse.)
  // (2)
  //   foo.h:  #define DEFINE_CLASS(classname) <backslash>
  //             struct classname { classname() { Init(); } void Init() {} }
  //   foo.cc: DEFINE_CLASS(Foo);
  // iwyu will say "Init() is a member function, so say we need the
  // full type information of the method's class."  The method's class
  // is Foo, which iwyu correctly declares lives in foo.cc.  But
  // iwyu also correctly says that Init() lives in foo.h (Except for
  // the macro arguments, macro code belongs to the macro definer,
  // not to every macro caller).  Put those together, though, and
  // iwyu says foo.h needs to #include foo.cc.
  // TODO(csilvers): it's probably more correct to check if
  // suggested_header() is in the transitive closure of actual_includes.
  // TODO(csilvers): this could cause breakage for code like this:
  //    x.cc:    class X {};
  //    y.h:     #include "x.cc"
  //    z.cc:    #include "y.h"; X x;
  // iwyu will say 'replace the #include of y.h with an #include of
  // x.cc,' which the code below will then strip.  The end result is
  // z.cc will not #include anything, and will fail to compile.
  if (!IsHeaderFile(use->suggested_header()) &&
      !ContainsKey(actual_includes, use->suggested_header())) {
    VERRS(6) << "Ignoring use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << "): #including .cc\n";
    use->set_ignore_use();
    return;
  }

  // (E2) Ignore use when the suggested header *is* the current file (this can
  // happen due to mappings).
  if (use_quoted_include == use->suggested_header()) {
    VERRS(6) << "Ignoring full use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc()
             << "): use already in suggested header "
             << use->suggested_header() << "\n";
    use->set_ignore_use();
    return;
  }

  // (E3) Mark iwyu violation unless in a current #include.
  if (ContainsKey(actual_includes, use->suggested_header())) {
    VERRS(6) << "Ignoring full use of " << use->symbol_name()
             << " (" << use->PrintableUseLoc() << "): #including dfn from "
             << use->suggested_header() << "\n";
  } else {
    use->set_is_iwyu_violation();
  }
}

}  // namespace internal

void IwyuFileInfo::CalculateIwyuViolations(vector<OneUse>* uses) {
  VERRS(6) << "--- Calculating IWYU violations for "
           << GetFilePath(file_) << " ---\n";

  // We have to do the steps in order, because a forward-declare use may
  // turn into a full use, and need to be processed in the full-use step
  // too.
  for (OneUse& use : *uses) {
    if (!use.is_full_use() && use.decl())
      internal::ProcessForwardDeclare(&use, preprocessor_info_);
  }
  for (OneUse& use : *uses) {
    if (use.is_full_use() && use.decl())
      internal::ProcessFullUse(&use, preprocessor_info_, *uses);
  }
  for (OneUse& use : *uses) {
    if (use.is_full_use() && !use.decl())
      internal::ProcessSymbolUse(&use, preprocessor_info_);
  }

  // (C1) Compute the direct includes of 'associated' files.
  set<string> associated_direct_includes;
  for (const IwyuFileInfo* associated : associated_headers_) {
    ReportIncludeFileUse(associated->file_, associated->quoted_file_,
                         OneUse::kAssociatedHeaderSynthetic);
    InsertAllInto(associated->direct_includes(), &associated_direct_includes);
  }
  // The 'effective' direct includes are defined to be the current
  // includes of associated, plus us.  This is only used to decide
  // when to give iwyu warnings.
  const set<string> effective_direct_includes =
      Union(associated_direct_includes, direct_includes());

  // (C2) + (C3) Find the minimal 'set cover' for all symbol uses.
  const set<string> desired_set_cover = internal::CalculateMinimalIncludes(
      quoted_file_, direct_includes(), associated_direct_includes, uses);

  // (C4) Remove .cc files from desired-includes unless they're in actual-inc.
  for (const string& header_name : desired_set_cover) {
    if (IsHeaderFile(header_name) ||
        ContainsKey(direct_includes(), header_name))
      desired_includes_.insert(header_name);
  }
  desired_includes_have_been_calculated_ = true;

  // The 'effective' desired includes are defined to be the desired
  // includes of associated, plus us.  These are used to decide if a
  // particular use will be satisfied after fixing the #includes.
  // NOTE: this depends on our associated headers having had their
  // iwyu analysis done before us.
  set<string> effective_desired_includes = desired_includes();
  InsertAllInto(AssociatedDesiredIncludes(), &effective_desired_includes);

  // Now that we've figured out desired_includes, figure out iwyu violations.
  for (OneUse& use : *uses) {
    if (use.ignore_use()) {
      // Do nothing, we're ignoring the use
    } else if (!use.is_full_use()) {
      internal::CalculateIwyuForForwardDeclareUse(
          &use, effective_direct_includes, effective_desired_includes,
          desired_includes(), AssociatedFileEntries(), preprocessor_info_);
    } else {
      internal::CalculateIwyuForFullUse(
          &use, effective_direct_includes, quoted_file_);
    }
  }
}

static string GetWarningDescription(const OneUse& use) {
  string warning;
  if (use.is_full_use()) {
    warning = use.symbol_name() + " is defined in " + use.suggested_header() +
              ", which isn't directly #included";
  } else {
    warning = use.symbol_name() + " needs a declaration, but does not provide "
              "or directly #include one";
  }
  if (!use.comment().empty())
    warning += " " + use.comment();
  return warning + ".";
}

static string GetWarningMsg(const OneUse& use) {
  const SourceLocation spelling_loc = GetSpellingLoc(use.use_loc());
  const SourceLocation instantiation_loc = GetInstantiationLoc(use.use_loc());
  string warning = PrintableLoc(spelling_loc) + ": warning: " +
                   GetWarningDescription(use) + "\n";
  if (instantiation_loc != spelling_loc) {
    // Only set/print this if it's different from the spelling location.
    warning += PrintableLoc(instantiation_loc) + ": note: used here.\n";
  }
  return warning;
}

int IwyuFileInfo::EmitWarningMessages(const vector<OneUse>& uses) {
  set<pair<int, string>> iwyu_warnings;   // line-number, warning-msg.
  for (const OneUse& use : uses) {
    if (use.is_iwyu_violation())
      iwyu_warnings.insert(make_pair(use.UseLinenum(), GetWarningMsg(use)));
  }
  // Nice that set<> automatically sorts things for us!
  for (const pair<int, string>& warning : iwyu_warnings) {
    if (ShouldPrint(3)) {
      errs() << warning.second;
    } else if (ShouldPrint(2)) {
      // TODO(csilvers): print one warning per sym per file.
    }
  }
  return iwyu_warnings.size();
}

namespace internal {

template <class IncludeOrFwdDecl>
bool Contains(const vector<OneIncludeOrForwardDeclareLine>& lines,
              const IncludeOrFwdDecl& item) {
  return std::any_of(lines.begin(), lines.end(),
                     [&](const OneIncludeOrForwardDeclareLine& line) {
    return line.matches(item);
  });
}

template <class ContainerType>
void ClearDesiredForSurplusIncludesOrForwardDeclares(ContainerType& container) {
  // Traverse multimap key by key.
  for (typename ContainerType::iterator k = container.begin();
       k != container.end(); k = container.upper_bound(k->first)) {
    // For each key, mark all but the first value as undesirable.
    typename ContainerType::iterator v = ++container.lower_bound(k->first);
    typename ContainerType::iterator vend = container.upper_bound(k->first);
    for (; v != vend; ++v) {
      v->second->clear_desired();
    }
  }
}

void CalculateDesiredIncludesAndForwardDeclares(
    const vector<OneUse>& uses,
    const set<string>& associated_desired_includes,
    const set<const FileEntry*>& kept_includes,
    vector<OneIncludeOrForwardDeclareLine>* lines) {
  // First make sure all uses' includes and fwd decls are reflected in lines.
  for (const OneUse& use : uses) {
    if (use.ignore_use())
      continue;

    if (use.is_full_use()) {
      CHECK_(use.has_suggested_header() && "Full uses should have #includes");
      if (!Contains(*lines, use.suggested_header())) { // must be added
        lines->push_back(OneIncludeOrForwardDeclareLine(
            use.decl_file(), use.suggested_header(), -1));
      }
    } else if (!use.has_suggested_header()) {
      // Forward-declare uses that are already satisfied by an #include
      // have that as their suggested_header.  For the rest, we need to
      // make sure there's a forward-declare in the current file.
      if (!Contains(*lines, use.decl())) { // must be added
        // The OneIncludeOrForwardDeclareLine ctor sets up line
        // numbers, but they're for some other file!  Clear them.
        lines->push_back(OneIncludeOrForwardDeclareLine(use.decl()));
        lines->back().clear_line_numbers();
      }
    }
  }

  // From this point on, lines is stable and we can refer to its
  // OneIncludeOrForwardDeclareLine objects by pointer.

  // We'll want to be able to map from an include or fwd-declare to the
  // lines where we found them.  There can be multiple includes or fwd-declares
  // providing the same symbol, so use multimaps for these reverse lookups.
  typedef multimap<string, OneIncludeOrForwardDeclareLine*> IncludeMap;
  typedef multimap<const NamedDecl*, OneIncludeOrForwardDeclareLine*>
      FwdDeclMap;

  IncludeMap include_map;
  FwdDeclMap fwd_decl_map;
  for (OneIncludeOrForwardDeclareLine& line : *lines) {
    if (line.IsIncludeLine())
      include_map.insert(std::make_pair(line.quoted_include(), &line));
    else
      fwd_decl_map.insert(std::make_pair(line.fwd_decl(), &line));
  }

  // Now run over all full uses and mark used includes as desired.
  for (const OneUse& use : uses) {
    if (use.ignore_use())
      continue;

    if (use.is_full_use()) {
      // Full uses need a proper include, so mark all corresponding include
      // lines as desired.
      auto range = include_map.equal_range(use.suggested_header());
      for (auto it = range.first; it != range.second; ++it) {
        it->second->set_desired();
        if (GlobalFlags().comments_with_namespace) {
          it->second->AddSymbolUse(use.symbol_name());
        } else {
          it->second->AddSymbolUse(use.short_symbol_name());
        }
      }
    }
  }

  // Mark forward-decl uses. We need to do this in a separate pass because
  // we need to be sure include_map is fully populated -- we don't want
  // to bother with a "(ptr only)" use if there's already a full use.
  for (const OneUse& use : uses) {
    if (use.ignore_use() || use.is_full_use())
      continue;

    if (!use.has_suggested_header()) {
      // A forward-declare for a use where there is no suggested header to
      // provide the symbol is very much desired.
      auto range = fwd_decl_map.equal_range(use.decl());
      for (auto it = range.first; it != range.second; ++it) {
        it->second->set_desired();
        it->second->mark_desired_by_use();
      }
    } else if (ContainsKey(include_map, use.suggested_header())) {
      // If we satisfy a forward-declare use from a file, let the file
      // know (this is just for logging).
      const string symbol_name = use.short_symbol_name();

      auto range = include_map.equal_range(use.suggested_header());
      for (auto it = range.first; it != range.second; ++it) {
        if (!it->second->HasSymbolUse(symbol_name))
          it->second->AddSymbolUse(symbol_name + " (ptr only)");
      }
    }
  }

  // If we #include a .h through an associated file (foo.h) rather
  // than directly (foo.cc), we don't want to say that .h is desired
  // -- that will cause us to add it when it's unnecessary.  We could
  // choose to actually *remove* the .h here if it's present, to keep
  // #includes to a minimum, but for now we just decline to add it.
  for (OneIncludeOrForwardDeclareLine& line : *lines) {
    if (line.is_desired() && !line.is_present() && line.IsIncludeLine() &&
        ContainsKey(associated_desired_includes, line.quoted_include())) {
      line.SetStructuredDecision(
          "add", "suppress", "provided_by_associated_header",
          "The associated header's desired state already provides this "
          "include.");
      line.clear_desired();
    }
  }

  // Clear desired for all duplicates.
  ClearDesiredForSurplusIncludesOrForwardDeclares(include_map);
  ClearDesiredForSurplusIncludesOrForwardDeclares(fwd_decl_map);

  // Now reset all files included with "IWYU pragma: keep" as desired.
  for (OneIncludeOrForwardDeclareLine& line : *lines) {
    if (!line.is_desired() && line.IsIncludeLine() &&
        ContainsKey(kept_includes, line.included_file())) {
      line.mark_restored_by_keep();
      line.set_desired();
    }
  }
}

bool IsRemovablePrefixHeader(const FileEntry* file_entry,
                             const IwyuPreprocessorInfo* preprocessor_info) {
  if (file_entry) {
    IwyuFileInfo* file_info = preprocessor_info->FileInfoFor(file_entry);
    if (file_info)
      return file_info->is_prefix_header() && !file_info->is_pch_in_code();
  }
  return false;
}

void CleanupPrefixHeaderIncludes(
    const IwyuPreprocessorInfo* preprocessor_info,
    vector<OneIncludeOrForwardDeclareLine>* lines) {
  CommandlineFlags::PrefixHeaderIncludePolicy policy =
      GlobalFlags().prefix_header_include_policy;
  if (policy == CommandlineFlags::kAdd)
    return;

  for (OneIncludeOrForwardDeclareLine& line : *lines) {
    if (!line.is_desired())
      continue;
    if (line.is_present() && (policy == CommandlineFlags::kKeep))
      continue;  // Keep present line according to policy.

    const FileEntry* file_entry = nullptr;
    if (line.IsIncludeLine()) {
      file_entry = line.included_file();
      if (!file_entry)
        file_entry = preprocessor_info->IncludeToFileEntry(
            line.quoted_include());
      // At this point it's OK if file_entry is nullptr.  It means we've never
      // seen quoted_include.  And that's why it cannot be prefix header.
    } else {
      const TagDecl* dfn = GetTagDefinition(line.fwd_decl());
      file_entry = GetFileEntry(dfn);
    }
    if (IsRemovablePrefixHeader(file_entry, preprocessor_info)) {
      CHECK_(file_entry && "FileEntry should exist to be prefix header");
      if (line.is_present()) {
        line.SetStructuredDecision(
            "remove", "apply", "prefix_header_remove",
            "The remove policy drops an in-source item already provided by "
            "a command-line prefix header.");
      } else {
        line.SetStructuredDecision(
            "add", "suppress", "prefix_header_add_suppressed",
            "Prefix-header policy suppresses adding an item already provided "
            "by a command-line prefix header.");
      }
      line.clear_desired();
      VERRS(6) << "Ignoring '" << line.line()
               << "': is superseded by command line include "
               << file_entry->getName() << "\n";
    }
  }
}

// Used by GetSymbolsSortedByFrequency().
class CountGt {
 public:
  bool operator()(const pair<string, int>& a,
                  const pair<string, int>& b) const {
    if (a.second != b.second)
      return a.second > b.second;   // sort by decreasing count
    return a.first < b.first;       // within a count, sort alphabetically
  }
};

// Given a map from string to count, creates a vector of the string
// keys, sorted by decreasing count (highest first), then alphabetically.
// We also can take a vector of forward-declared symbols used by this
// file.  For all symbols in this vector but not in m, we add them to
// the end of the output as well, with a "(ptr only)" suffix.
vector<string> GetSymbolsSortedByFrequency(const map<string, int>& m) {
  vector<pair<string, int>> count_vector(m.begin(), m.end());
  sort(count_vector.begin(), count_vector.end(), CountGt());

  vector<string> retval;
  for (const pair<string, int>& count : count_vector)
    retval.push_back(count.first);
  return retval;
}

OutputLine PrintableIncludeOrForwardDeclareLine(
    const OneIncludeOrForwardDeclareLine& line,
    const set<string>& associated_quoted_includes) {
  // If we don't want any comments, always use the line as-is.
  if (GlobalFlags().no_comments) {
    return OutputLine(line.line());
  }

  // Print the line number where we saw this forward-declare or
  // #include, as a comment, if we don't have anything better to show.
  // (For instance, when we want to delete this line.)
  if (line.symbol_counts().empty() && !line.is_present()) {
    return OutputLine(line.line());   // if not present, doesn't have a line #
  }
  if (line.symbol_counts().empty() || !line.is_desired()) {
    CHECK_(!StartsWith(line.LineNumberString(), "-"));
    return OutputLine(line.line() + "  // lines " + line.LineNumberString());
  }
  // We don't need to explain why foo.cc #includes foo.h
  if (line.IsIncludeLine() &&
      ContainsKey(associated_quoted_includes, line.quoted_include())) {
    return OutputLine(line.line());
  }

  return OutputLine(line.line(),
    GetSymbolsSortedByFrequency(line.symbol_counts()));
}

enum class LineSortOrdinal {
  PrecompiledHeader,
  AssociatedHeader,
  AssociatedInlineDefinitions,
  QuotedInclude,
  CHeader,
  CppHeader,
  OtherHeader,
  ForwardDeclaration
};

using LineSortKey = pair<LineSortOrdinal, string>;

LineSortOrdinal GetLineSortOrdinal(const OneIncludeOrForwardDeclareLine& line,
                                   const set<string>& associated_quoted_includes,
                                   const IwyuFileInfo* file_info) {
  if (!line.IsIncludeLine())
    return LineSortOrdinal::ForwardDeclaration;
  if (file_info && file_info->is_pch_in_code())
    return LineSortOrdinal::PrecompiledHeader;

  const std::string quoted_include = line.quoted_include();
  if (ContainsKey(associated_quoted_includes, quoted_include)) {
    if (IsInlineHeaderFile(quoted_include))
      return LineSortOrdinal::AssociatedInlineDefinitions;
    return LineSortOrdinal::AssociatedHeader;
  }

  if (GlobalFlags().quoted_includes_first && EndsWith(quoted_include, "\""))
    return LineSortOrdinal::QuotedInclude;
  if (EndsWith(quoted_include, ".h>"))
    return LineSortOrdinal::CHeader;
  if (EndsWith(quoted_include, ">"))
    return LineSortOrdinal::CppHeader;
  return LineSortOrdinal::OtherHeader;
}

// The sort key of an include/forward-declare line is a (LineSortOrdinal, string)
// pair.  The string is always the line itself.
LineSortKey GetSortKey(const OneIncludeOrForwardDeclareLine& line,
                       const set<string>& associated_quoted_includes,
                       const IwyuFileInfo* file_info) {
  return LineSortKey(GetLineSortOrdinal(line, associated_quoted_includes, file_info), line.line());
}

// filename is "this" filename: the file being emitted.
// associated_filepaths are the quoted-include form of associated_headers_.
size_t PrintableDiffs(const string& filename,
                      const IwyuPreprocessorInfo* preprocessor_info,
                      const set<string>& associated_quoted_includes,
                      const vector<OneIncludeOrForwardDeclareLine>& lines,
                      string* diff_output) {
  CHECK_(diff_output && "Must provide diff_output");

  string& output = *diff_output;
  vector<OutputLine> output_lines;

  const set<string>& aqi = associated_quoted_includes;  // short alias

  // Sort all the output-lines: system headers before user headers
  // before forward-declares, etc.  The easiest way to do this is to
  // just put them all in multimap whose key is a sort-order (multimap
  // because some headers might be listed twice in the source file.)
  multimap<LineSortKey, const OneIncludeOrForwardDeclareLine*> sorted_lines;
  for (const OneIncludeOrForwardDeclareLine& line : lines) {
    const IwyuFileInfo* file_info = nullptr;
    if (line.IsIncludeLine())
      file_info = preprocessor_info->FileInfoFor(line.included_file());

    sorted_lines.insert(make_pair(GetSortKey(line, aqi, file_info), &line));
  }

  // First, check if there are no adds or deletes.  If so, we print a
  // shorter summary line.
  bool no_adds_or_deletes = true;
  for (const auto& key_line : sorted_lines) {
    const OneIncludeOrForwardDeclareLine* line = key_line.second;
    if ((line->is_desired() && !line->is_present()) || // add
        (line->is_present() && !line->is_desired())) { // delete
      no_adds_or_deletes = false;
      break;
    }
  }
  if (no_adds_or_deletes && !GlobalFlags().update_comments) {
    output = "\n(" + filename + " has correct #includes/fwd-decls)\n";
    return 0;
  }

  size_t num_edits = 0;

  // First, new desired includes and forward-declares.
  if (ShouldPrint(1)) {
    output_lines.push_back(
      OutputLine("\n" + filename + " should add these lines:"));
    for (const auto& key_line : sorted_lines) {
      const OneIncludeOrForwardDeclareLine* line = key_line.second;
      if (line->is_desired() && !line->is_present()) {
        output_lines.push_back(
          PrintableIncludeOrForwardDeclareLine(*line, aqi));
        ++num_edits;
      }
    }
  }

  // Second, includes and forward-declares that should be removed.
  if (ShouldPrint(1)) {
    output_lines.push_back(
        OutputLine("\n" + filename + " should remove these lines:"));
    for (const auto& key_line : sorted_lines) {
      const OneIncludeOrForwardDeclareLine* line = key_line.second;
      if (line->is_present() && !line->is_desired()) {
        output_lines.push_back(
            PrintableIncludeOrForwardDeclareLine(*line, aqi));
        output_lines.back().add_prefix("- ");

        ++num_edits;
      }
    }
  }

  // Finally, print the final, complete include-and-forward-declare list.
  if (ShouldPrint(0)) {
    output_lines.push_back(
      OutputLine("\nThe full include-list for " + filename + ":"));
    for (const auto& key_line : sorted_lines) {
      const OneIncludeOrForwardDeclareLine* line = key_line.second;
      if (line->is_desired() && !line->is_elaborated_type()) {
        output_lines.push_back(
          PrintableIncludeOrForwardDeclareLine(*line, aqi));
      }
    }
  }

  // Compute max width of lines with comments so we can align them nicely.
  // This is not strictly necessary if comments have been disabled,
  // but it won't have any effect in that case anyway.
  size_t line_length = 0;
  size_t max_line_length = GlobalFlags().max_line_length;

  for (const OutputLine& line : output_lines) {
    // Only consider lines that need alignment.
    if (line.needs_alignment())
      line_length = std::max(line.line_length(), line_length);
  }

  // Align lines and produce final output.
  for (const OutputLine& line : output_lines) {
    output += line.printable_line(line_length, max_line_length);
    output += "\n";
  }

  // Let's print a helpful separator as well.
  output += "---\n";

  return num_edits;
}

}  // namespace internal

void IwyuFileInfo::HandlePreprocessingDone() {
  // Check macros defined by includer.  Requires file preprocessing to be
  // finished to know all direct includes and all macro usages.
  //
  // Exclude prefix headers from mapping heuristics.  Includes in prefix
  // headers are kept regardless of their usage in includer.  And the entire
  // include-what-you-use principle isn't really applicable to prefix headers.
  if (is_prefix_header()) {
    return;
  }
  bool should_report_violations = ShouldReportIWYUViolationsFor(file_);
  std::list<const FileEntry*> direct_macro_use_includees;
  std::set_intersection(macro_users_.begin(), macro_users_.end(),
                        direct_includes_as_fileentries_.begin(),
                        direct_includes_as_fileentries_.end(),
                        std::inserter(direct_macro_use_includees,
                                      direct_macro_use_includees.end()));
  for (const FileEntry* macro_use_includee : direct_macro_use_includees) {
    if (should_report_violations) {
      ERRSYM(file_) << "Keep #include " << macro_use_includee->getName()
                    << " in " << file_->getName()
                    << " because used macro is defined by includer.\n";
      ReportKnownDesiredFile(
          macro_use_includee, "reverse_macro_dependency",
          "A macro used by this include is defined by the includer.");
    } else {
      string private_include =
          ConvertToQuotedInclude(GetFilePath(macro_use_includee));
      if (GlobalIncludePicker().IsPublic(macro_use_includee)) {
        ERRSYM(file_) << "Skip marking " << quoted_file_
                      << " as public header for " << private_include
                      << " because latter is already marked as public,"
                      << " though uses macro defined by includer.\n";
      } else if (file_ == macro_use_includee) {
        ERRSYM(file_) << "Skip marking " << quoted_file_
                      << " as public header for " << private_include
                      << " because they are the same file.\n";
      } else {
        ERRSYM(file_) << "Mark " << quoted_file_
                      << " as public header for " << private_include
                      << " because used macro is defined by includer.\n";
        VERRS(8) << "Adding dynamic mapping for reverse macro dependency: "
                 << "(" << GetFilePath(macro_use_includee) << ") -> ("
                 << GetFilePath(file_) << ")\n";
        MutableGlobalIncludePicker()->AddMapping(
            private_include, MappedInclude(quoted_file_, GetFilePath(file_)));
        MutableGlobalIncludePicker()->MarkIncludeAsPrivate(private_include);
      }
    }
  }
}

void IwyuFileInfo::ResolvePendingAnalysis() {
  // Resolve using declarations:  This handles the case where there's a using
  // declaration in the file but no code is actually using it. If that
  // happens, we might try to remove all of the headers with the decls that
  // the using decl is referencing, which would result in a compilation error
  // at best. A possible solution is to remove the using decl if it's not
  // used, but that doesn't work for header files because a using decl in a
  // header is an exported symbol, so we don't want to do that either. As a
  // compromise, we arbitrarily add the first shadow decl to make sure
  // everything still compiles instead of removing the using decl. A more
  // thorough approach would be to scan the current list of includes that
  // already name this decl (like in the overloaded function case) and include
  // one of those so we don't include a file we don't actually need.
  for (map<const UsingDecl*, bool>::value_type using_decl_status
      : using_decl_referenced_) {
    if (!using_decl_status.second) {
      // There are valid cases where there is no shadow decl, e.g. if a derived
      // class has a using declaration for a member, but also hides it.
      // Only report the target if we have a shadow decl.
      const UsingDecl* using_decl = using_decl_status.first;
      if (using_decl->shadow_size() > 0) {
        ReportForwardDeclareUse(using_decl->getUsingLoc(),
                                using_decl->shadow_begin()->getTargetDecl(),
                                /* flags */ UF_None,
                                "(for un-referenced using)");

      }
    }
  }
}

namespace {

bool HasStableDeclarationIdentity(const OneUse& use) {
  if (use.ignore_use() || !use.decl() || use.symbol_name().empty())
    return false;
  for (const DeclContext* context = use.decl()->getDeclContext(); context;
       context = context->getParent()) {
    if (context->isFunctionOrMethod())
      return false;
  }
  return true;
}

const NamedDecl* StructuredDeclarationIdentity(const NamedDecl* decl) {
  if (const ClassTemplateSpecializationDecl* specialization =
          dyn_cast<ClassTemplateSpecializationDecl>(decl)) {
    return specialization->getSpecializedTemplate();
  }
  if (const CXXRecordDecl* record = dyn_cast<CXXRecordDecl>(decl)) {
    if (record->getDescribedClassTemplate())
      return record->getDescribedClassTemplate();
  }
  return decl;
}

string StructuredDeclarationKind(const NamedDecl* input_decl) {
  const NamedDecl* decl = StructuredDeclarationIdentity(input_decl);
  if (!decl)
    return string();
  if (isa<ClassTemplateDecl>(decl))
    return "class_template";
  if (isa<TypeAliasDecl>(decl))
    return "using_alias";
  if (isa<TypedefDecl>(decl))
    return "typedef";
  if (const EnumDecl* enum_decl = dyn_cast<EnumDecl>(decl))
    return enum_decl->isScoped() ? "scoped_enum" : "unscoped_enum";
  if (const CXXRecordDecl* record_decl = dyn_cast<CXXRecordDecl>(decl)) {
    if (record_decl->getDescribedClassTemplate())
      return "class_template";
    if (record_decl->isUnion())
      return "union";
    if (record_decl->isStruct())
      return "struct";
    if (record_decl->isClass())
      return "class";
  }
  return string();
}

string StructuredSourceIdentity(const FileEntry* file) {
  const llvm::StringRef real_path = file->tryGetRealPathName();
  CHECK_(!real_path.empty() &&
         "analyzed source must have a canonical physical identity");
  return NormalizeFilePath(real_path.str());
}

string StructuredSourceDigest(const FileEntry* file) {
  clang::SourceManager& source_manager = *GlobalSourceManager();
  const clang::FileID file_id = source_manager.translateFile(file);
  bool invalid = false;
  const llvm::StringRef buffer = source_manager.getBufferData(file_id, &invalid);
  CHECK_(!invalid && "analyzed source buffer must remain available");
  const auto bytes = llvm::ArrayRef<uint8_t>(
      reinterpret_cast<const uint8_t*>(buffer.data()), buffer.size());
  return "sha256:" + llvm::toHex(llvm::SHA256::hash(bytes), true);
}

llvm::json::Object UnavailableValue() {
  return llvm::json::Object{{"status", "unavailable"}, {"value", nullptr}};
}

llvm::json::Object NotApplicableValue() {
  return llvm::json::Object{{"status", "not_applicable"},
                            {"value", nullptr}};
}

struct StructuredLocationValue {
  string path;
  int64_t line;
  int64_t column;
};

bool StructuredLocation(SourceLocation location,
                        bool use_expansion_location,
                        StructuredLocationValue* result) {
  if (location.isInvalid())
    return false;
  const SourceLocation resolved = use_expansion_location
      ? GetInstantiationLoc(location) : GetSpellingLoc(location);
  if (resolved.isInvalid())
    return false;
  const clang::FileEntry* file = GetLocFileEntry(resolved);
  if (!file)
    return false;
  const clang::FullSourceLoc full_location(resolved, *GlobalSourceManager());
  bool invalid = false;
  const unsigned line = full_location.getSpellingLineNumber(&invalid);
  if (invalid || line == 0)
    return false;
  const unsigned column = full_location.getSpellingColumnNumber(&invalid);
  if (invalid || column == 0)
    return false;
  *result = {NormalizeFilePath(GetFilePath(file)),
             static_cast<int64_t>(line), static_cast<int64_t>(column)};
  return true;
}

llvm::json::Object LocationObject(const StructuredLocationValue& location) {
  return llvm::json::Object{{"path", location.path},
                            {"line", location.line},
                            {"column", location.column}};
}

llvm::json::Object AvailableLocation(SourceLocation location,
                                     bool use_expansion_location) {
  StructuredLocationValue value;
  if (!StructuredLocation(location, use_expansion_location, &value))
    return UnavailableValue();
  return llvm::json::Object{{"status", "known"},
                            {"value", LocationObject(value)}};
}

llvm::json::Object ConditionObject(
    const IwyuPreprocessorInfo::ActiveCondition& condition) {
  using Status = IwyuPreprocessorInfo::ActiveCondition::Status;
  if (condition.status == Status::kNotApplicable)
    return NotApplicableValue();
  if (condition.status == Status::kUnknown)
    return llvm::json::Object{{"status", "unknown"}, {"value", nullptr}};
  llvm::json::Object value{
      {"expression", condition.expression},
      {"location", AvailableLocation(condition.directive_location, false)},
      {"active_branch", condition.active_branch}};
  if (GlobalFlags().output_schema_version >= 2) {
    llvm::json::Array source_region_path;
    for (const string& region : condition.source_region_path)
      source_region_path.push_back(region);
    value["source_region_id"] = condition.source_region_id;
    value["source_region_path"] = std::move(source_region_path);
  }
  return llvm::json::Object{
      {"status", "known"}, {"value", std::move(value)}};
}

ProjectPolicyActiveCondition PolicyActiveCondition(
    const IwyuPreprocessorInfo::ActiveCondition& condition) {
  using InputStatus = IwyuPreprocessorInfo::ActiveCondition::Status;
  ProjectPolicyActiveCondition result;
  switch (condition.status) {
    case InputStatus::kNotApplicable:
      result.availability =
          ProjectPolicyConditionAvailability::NotApplicable;
      break;
    case InputStatus::kKnown:
      result.availability = ProjectPolicyConditionAvailability::Known;
      result.expression = condition.expression;
      break;
    case InputStatus::kUnknown:
      result.availability = ProjectPolicyConditionAvailability::Unknown;
      break;
  }
  return result;
}

ProjectPolicyEditLexicalScope PolicyEditLexicalScope(
    const IwyuPreprocessorInfo* preprocessor_info, const FileEntry* file,
    const IwyuPreprocessorInfo::ActiveCondition& condition) {
  using ConditionStatus = IwyuPreprocessorInfo::ActiveCondition::Status;
  using Activity = IwyuPreprocessorInfo::BranchActivity;
  using Scope = IwyuPreprocessorInfo::LexicalScope;
  ProjectPolicyEditLexicalScope result;
  if (condition.status == ConditionStatus::kNotApplicable) {
    result.availability =
        ProjectPolicyLexicalScopeAvailability::NotApplicable;
    return result;
  }
  if (condition.status != ConditionStatus::kKnown ||
      !condition.active_branch || condition.source_region_path.empty() ||
      condition.source_region_id != condition.source_region_path.back()) {
    return result;
  }

  SourceLocation directive =
      GlobalSourceManager()->getExpansionLoc(condition.directive_location);
  if (directive.isInvalid() || !directive.isFileID())
    return result;
  const clang::FileID condition_file_id =
      GlobalSourceManager()->getFileID(directive);
  if (condition_file_id.isInvalid())
    return result;
  const unsigned exact_file_id = condition_file_id.getHashValue();
  const vector<IwyuPreprocessorInfo::ConditionalRegion> census =
      preprocessor_info->ConditionalRegionsFor(file);

  bool any_non_file = false;
  string expected_parent;
  for (const string& source_region_id : condition.source_region_path) {
    const IwyuPreprocessorInfo::ConditionalRegion* exact = nullptr;
    size_t count = 0;
    for (const auto& region : census) {
      if (region.file_id == exact_file_id &&
          region.source_region_id == source_region_id) {
        exact = &region;
        ++count;
      }
    }
    if (count != 1 || !exact ||
        exact->parent_source_region_id != expected_parent ||
        exact->activity != Activity::kActive ||
        exact->lexical_scope == Scope::kUnknown) {
      return result;
    }
    any_non_file = any_non_file ||
                   exact->lexical_scope == Scope::kNonFile;
    expected_parent = source_region_id;
  }

  result.availability = ProjectPolicyLexicalScopeAvailability::Known;
  result.scope = any_non_file ? "non_file" : "file";
  return result;
}

llvm::json::Object KnownString(const string& value) {
  return llvm::json::Object{{"status", "known"}, {"value", value}};
}

llvm::json::Object UnavailableDesiredState() {
  return llvm::json::Object{{"status", "unavailable"},
                            {"complete", false}, {"items", nullptr}};
}

}  // namespace

string IwyuFileInfo::file_path() const {
  return NormalizeFilePath(GetFilePath(file_));
}

void IwyuFileInfo::EmitStructuredOutputRecord() const {
  struct OperationRef {
    const OneIncludeOrForwardDeclareLine* line;
    string subject_action;
    string disposition;
    string item_kind;
    string reason_code;
    string reason;
  };

  const auto item_kind = [](const OneIncludeOrForwardDeclareLine& line) {
    return line.IsIncludeLine() ? string("include")
                                : string("forward_declaration");
  };

  map<pair<string, string>, int> present_occurrences;
  for (const OneIncludeOrForwardDeclareLine& line : lines_) {
    if (line.is_present() && !line.is_elaborated_type())
      ++present_occurrences[{item_kind(line), line.line()}];
  }
  const bool has_duplicate_occurrence = std::any_of(
      present_occurrences.begin(), present_occurrences.end(),
      [](const auto& occurrence) { return occurrence.second > 1; });
  // Version 1 has only key-level identity and cannot distinguish duplicate
  // physical occurrences. Version 2 identifies present operations by their
  // exact source range and condition, so it can express occurrence relocation.
  const bool has_unrepresentable_duplicate =
      GlobalFlags().output_schema_version == 1 && has_duplicate_occurrence;

  vector<OperationRef> operations;
  if (!has_unrepresentable_duplicate) {
    for (const OneIncludeOrForwardDeclareLine& line : lines_) {
      if (line.is_elaborated_type())
        continue;
      if (line.has_structured_decision()) {
        operations.push_back(
            {&line, line.decision_subject_action(),
             line.decision_disposition(), item_kind(line),
             line.decision_reason_code(), line.decision_reason()});
        continue;
      }
      if (line.is_desired() == line.is_present())
        continue;
      if (line.is_present()) {
        operations.push_back({&line, "remove", "apply", item_kind(line),
                              "undesired_item_present",
                              "Present item is absent from IWYU's desired "
                              "state."});
      } else {
        operations.push_back({&line, "add", "apply", item_kind(line),
                              "desired_item_missing",
                              "IWYU's desired item is absent from the file."});
      }
    }
  }

  if (GlobalFlags().output_schema_version >= 2 && has_duplicate_occurrence) {
    for (const OneIncludeOrForwardDeclareLine& line : lines_) {
      if (line.is_elaborated_type() || !line.is_present() ||
          !line.is_desired() || line.has_structured_decision()) {
        continue;
      }
      const auto found = present_occurrences.find({item_kind(line), line.line()});
      if (found == present_occurrences.end() || found->second < 2)
        continue;
      operations.push_back(
          {&line, "keep", "keep", item_kind(line), "retained_occurrence",
           "This physical occurrence remains in the desired final state."});
    }
  }

  const auto operation_condition = [&](const OperationRef& operation) {
    using ActiveCondition = IwyuPreprocessorInfo::ActiveCondition;
    if (operation.line->is_present()) {
      if (operation.line->IsIncludeLine()) {
        return preprocessor_info_->DirectIncludeConditionAt(
            file_, operation.line->quoted_include(),
            operation.line->start_linenum());
      }
      if (operation.line->fwd_decl())
        return preprocessor_info_->ActiveConditionAt(
            GetLocation(operation.line->fwd_decl()));
      return ActiveCondition{};
    }

    bool found = false;
    ActiveCondition common;
    for (const OneUse& use : symbol_uses_) {
      if (use.ignore_use() || use.symbol_name().empty())
        continue;
      const bool motivates = operation.line->IsIncludeLine()
          ? use.has_suggested_header() &&
                use.suggested_header() == operation.line->quoted_include()
          : !use.is_full_use() && !use.has_suggested_header() &&
                use.decl() == operation.line->fwd_decl();
      if (!motivates)
        continue;
      const ActiveCondition condition =
          preprocessor_info_->ActiveConditionAt(use.use_loc());
      if (!found) {
        common = condition;
        found = true;
        continue;
      }
      if (common.status != condition.status ||
          common.expression != condition.expression ||
          common.directive_location != condition.directive_location ||
          common.active_branch != condition.active_branch ||
          common.source_region_id != condition.source_region_id ||
          common.source_region_path != condition.source_region_path) {
        return ActiveCondition{};
      }
    }
    return found ? common : ActiveCondition{};
  };

  const auto operation_condition_key = [&](const OperationRef& operation) {
    using Status = IwyuPreprocessorInfo::ActiveCondition::Status;
    const auto condition = operation_condition(operation);
    const int status_rank = condition.status == Status::kKnown
        ? 0 : (condition.status == Status::kUnknown ? 1 : 3);
    StructuredLocationValue location;
    if (condition.status != Status::kKnown ||
        !StructuredLocation(condition.directive_location, false, &location)) {
      location = StructuredLocationValue{"", 0, 0};
    }
    return std::make_tuple(
        status_rank, condition.expression, location.path, location.line,
        location.column, condition.active_branch, condition.source_region_id,
        condition.source_region_path);
  };

  const auto operation_key = [&](const OperationRef& operation) {
    const map<string, int> subject_rank = {
        {"remove", 0}, {"add", 1}, {"keep", 2}};
    const map<string, int> disposition_rank = {
        {"apply", 0}, {"keep", 1}, {"suppress", 2}, {"report", 3}};
    const int kind_rank = operation.item_kind == "include" ? 0 : 1;
    // Native v1 removal ranges are unavailable and addition ranges are
    // not-applicable, so spelling is the next stable key.
    const bool exact_present_spelling =
        operation.line->is_present() && operation.subject_action != "add";
    const string spelling = exact_present_spelling
                                ? operation.line->source_spelling()
                                : operation.line->line();
    const bool semantic_range =
        operation.reason_code == "semantic_definition_provider";
    const bool occurrence_range = semantic_range ||
        (GlobalFlags().output_schema_version >= 2 &&
         operation.subject_action != "add" && operation.line->is_present() &&
         operation.line->start_linenum() > 0);
    const int range_rank = occurrence_range ? 0 :
        (operation.subject_action == "add" ? 3 : 2);
    const int range_line =
        occurrence_range ? operation.line->start_linenum() : 0;
    int range_column = occurrence_range ? 1 : 0;
    if (semantic_range) {
      const auto found = semantic_definition_provider_facts_.find(
          operation.line->start_linenum());
      if (found != semantic_definition_provider_facts_.end() &&
          !found->second.empty()) {
        StructuredLocationValue range_location;
        if (StructuredLocation(found->second.begin()->second.include_location,
                               false,
                               &range_location)) {
          range_column = range_location.column;
        }
      }
    }
    return std::make_tuple(subject_rank.at(operation.subject_action),
                           disposition_rank.at(operation.disposition),
                           kind_rank, range_rank,
                           semantic_range ? file_path() : string(),
                           range_line, range_column, range_line,
                           semantic_range
                               ? range_column +
                                     operation.line->quoted_include().size()
                               : 0,
                           spelling, operation.reason_code,
                           operation_condition_key(operation));
  };

  const auto operation_spelling = [](const OperationRef& operation) {
    // Operations on present items identify their exact physical source text.
    // Generated additions remain canonical.
    if (operation.line->is_present() && operation.subject_action != "add")
      return operation.line->source_spelling();
    return operation.line->line();
  };

  const auto operation_symbols = [&](const OperationRef& operation) {
    if (operation.reason_code == "semantic_definition_provider") {
      struct SemanticFact {
        const SemanticDefinitionProviderFact* fact;
        string name;
        string qualified_name;
        StructuredLocationValue use_location;
        StructuredLocationValue declaration;
        IwyuPreprocessorInfo::ActiveCondition condition;
      };
      vector<SemanticFact> facts;
      const auto found = semantic_definition_provider_facts_.find(
          operation.line->start_linenum());
      if (found == semantic_definition_provider_facts_.end())
        return llvm::json::Array{};
      for (const auto& entry : found->second) {
        const SemanticDefinitionProviderFact& fact = entry.second;
        for (const auto& use_entry : fact.uses) {
          const SemanticDefinitionProviderUseFact& use = use_entry.second;
          StructuredLocationValue use_location;
          StructuredLocationValue declaration;
          if (!StructuredLocation(use.use_location, true, &use_location) ||
              !StructuredLocation(use.definition_location, false,
                                  &declaration) ||
              use_location.path != file_path()) {
            continue;
          }
          facts.push_back({
              &fact, fact.function->getNameAsString(),
              fact.function->getQualifiedNameAsString(), use_location,
              declaration,
              preprocessor_info_->ActiveConditionAt(use.use_location)});
        }
      }
      const auto fact_key = [](const SemanticFact& fact) {
        return std::make_tuple(
            fact.qualified_name, fact.declaration.path,
            fact.declaration.line, fact.declaration.column, fact.name);
      };
      std::sort(facts.begin(), facts.end(),
                [&](const SemanticFact& lhs, const SemanticFact& rhs) {
                  return fact_key(lhs) < fact_key(rhs);
                });

      llvm::json::Array symbols;
      size_t begin = 0;
      while (begin < facts.size()) {
        size_t end = begin + 1;
        while (end < facts.size() &&
               facts[end].qualified_name == facts[begin].qualified_name) {
          ++end;
        }
        vector<llvm::json::Object> projected_uses;
        projected_uses.reserve(end - begin);
        for (size_t index = begin; index < end; ++index) {
          projected_uses.push_back(llvm::json::Object{
              {"location", LocationObject(facts[index].use_location)},
              {"requirement", "full"},
              {"origin", "ast"},
              {"condition", ConditionObject(facts[index].condition)},
              {"macro", UnavailableValue()}});
        }
        // fact_key puts the exact canonical emitted declaration first. This
        // deliberately collapses redeclarations/instantiations sharing the
        // stable qualified identity instead of emitting duplicate symbols.
        const SemanticFact& first = facts[begin];
        llvm::json::Object symbol{
            {"name", first.name},
            {"qualified_name", KnownString(first.qualified_name)},
            {"declaration", llvm::json::Object{
                {"status", "known"},
                {"value", LocationObject(first.declaration)}}},
            {"uses", internal::SortAndDeduplicateProjectedUses(
                         std::move(projected_uses))}};
        if (GlobalFlags().output_schema_version >= 2)
          symbol["declaration_kind"] = UnavailableValue();
        symbols.push_back(std::move(symbol));
        begin = end;
      }
      return symbols;
    }
    if (operation.reason_code == "macro_body_dependency_unavailable")
      return llvm::json::Array{};
    if (operation.reason_code == "macro_body_dependency") {
      struct MacroFact {
        const MacroBodyDependencyFact* fact;
        StructuredLocationValue declaration;
        StructuredLocationValue reference;
        StructuredLocationValue definition;
        IwyuPreprocessorInfo::ActiveCondition condition;
      };
      vector<MacroFact> facts;
      const auto found = macro_body_dependency_facts_.find(
          operation.line->included_file());
      if (found != macro_body_dependency_facts_.end()) {
        for (const MacroBodyDependencyFact& fact : found->second) {
          StructuredLocationValue declaration;
          StructuredLocationValue reference;
          StructuredLocationValue definition;
          if (!StructuredLocation(fact.declaration_location, false,
                                  &declaration) ||
              !StructuredLocation(fact.reference_location, false, &reference) ||
              !StructuredLocation(fact.macro_definition_location, false,
                                  &definition) ||
              reference.path != file_path() || definition.path != file_path()) {
            continue;
          }
          facts.push_back(
              {&fact, declaration, reference, definition,
               preprocessor_info_->ActiveConditionAt(
                   fact.macro_definition_location)});
        }
      }
      const auto fact_key = [](const MacroFact& fact) {
        return std::make_tuple(
            fact.fact->qualified_name, fact.declaration.path,
            fact.declaration.line, fact.declaration.column,
            fact.fact->symbol_name, fact.reference.path, fact.reference.line,
            fact.reference.column, fact.fact->macro_name,
            fact.definition.path, fact.definition.line, fact.definition.column,
            static_cast<int>(fact.condition.status), fact.condition.expression);
      };
      std::sort(facts.begin(), facts.end(),
                [&](const MacroFact& lhs, const MacroFact& rhs) {
                  return fact_key(lhs) < fact_key(rhs);
                });

      llvm::json::Array symbols;
      size_t begin = 0;
      while (begin < facts.size()) {
        size_t end = begin + 1;
        while (end < facts.size() &&
               facts[end].fact->qualified_name ==
                   facts[begin].fact->qualified_name) {
          ++end;
        }
        vector<llvm::json::Object> projected_uses;
        projected_uses.reserve(end - begin);
        for (size_t index = begin; index < end; ++index) {
          const MacroFact& fact = facts[index];
          projected_uses.push_back(llvm::json::Object{
              {"location", LocationObject(fact.reference)},
              {"requirement", "unknown"},
              {"origin", "macro"},
              {"condition", ConditionObject(fact.condition)},
              {"macro", llvm::json::Object{
                  {"status", "known"},
                  {"value", llvm::json::Object{
                      {"name", fact.fact->macro_name},
                      {"definition", llvm::json::Object{
                          {"status", "known"},
                          {"value", LocationObject(fact.definition)}}},
                      {"expansion", NotApplicableValue()}}}}}});
        }
        const MacroFact& first = facts[begin];
        llvm::json::Object symbol{
            {"name", first.fact->symbol_name},
            {"qualified_name", KnownString(first.fact->qualified_name)},
            {"declaration", llvm::json::Object{
                {"status", "known"},
                {"value", LocationObject(first.declaration)}}},
            {"uses", internal::SortAndDeduplicateProjectedUses(
                         std::move(projected_uses))}};
        if (GlobalFlags().output_schema_version >= 2)
          symbol["declaration_kind"] = UnavailableValue();
        symbols.push_back(std::move(symbol));
        begin = end;
      }
      return symbols;
    }

    struct UseFact {
      const OneUse* use;
      string name;
      bool has_qualified_name;
      string qualified_name;
      StructuredLocationValue location;
      IwyuPreprocessorInfo::ActiveCondition condition;
    };
    vector<UseFact> facts;
    for (const OneUse& use : symbol_uses_) {
      if (use.ignore_use() || use.symbol_name().empty())
        continue;
      bool motivates = false;
      if (operation.line->IsIncludeLine()) {
        motivates = use.has_suggested_header() &&
            use.suggested_header() == operation.line->quoted_include();
        if (!motivates && GlobalFlags().output_schema_version >= 2 &&
            operation.subject_action == "remove" && !use.is_full_use() &&
            !use.has_suggested_header() &&
            use.decl_file() == operation.line->included_file()) {
          // A direct provider removed in favor of a forward declaration must
          // carry that entity identity so a multi-config driver can suppress
          // the exact paired removal without path-spelling heuristics.
          motivates = true;
        }
      } else {
        motivates = !use.is_full_use() && !use.has_suggested_header() &&
            use.decl() == operation.line->fwd_decl();
      }
      if (!motivates)
        continue;
      StructuredLocationValue location;
      if (!StructuredLocation(use.use_loc(), true, &location) ||
          location.path != file_path()) {
        continue;
      }
      const string name = use.short_symbol_name().empty()
          ? use.symbol_name() : use.short_symbol_name();
      const bool canonical_declaration_identity =
          GlobalFlags().output_schema_version >= 2 && use.decl();
      facts.push_back({
          &use, name, use.decl() != nullptr,
          canonical_declaration_identity
              ? internal::GetQualifiedNameAsString(
                    StructuredDeclarationIdentity(use.decl()))
              : use.symbol_name(),
          location, preprocessor_info_->ActiveConditionAt(use.use_loc())});
    }
    const auto fact_key = [](const UseFact& fact) {
      const string primary = fact.has_qualified_name
          ? fact.qualified_name : fact.name;
      const int requirement = fact.use->is_full_use() ? 0 : 1;
      const int identity_availability = fact.has_qualified_name ? 0 : 1;
      return std::make_tuple(
          primary, fact.name, fact.location.path, fact.location.line,
          fact.location.column, requirement, identity_availability,
          static_cast<int>(fact.condition.status), fact.condition.expression);
    };
    std::sort(facts.begin(), facts.end(),
              [&](const UseFact& lhs, const UseFact& rhs) {
                return fact_key(lhs) < fact_key(rhs);
              });

    llvm::json::Array symbols;
    size_t begin = 0;
    while (begin < facts.size()) {
      size_t end = begin + 1;
      const string group_identity = facts[begin].has_qualified_name
          ? facts[begin].qualified_name : facts[begin].name;
      while (end < facts.size() && facts[end].name == facts[begin].name &&
             (facts[end].has_qualified_name
                  ? facts[end].qualified_name : facts[end].name) ==
                 group_identity) {
        ++end;
      }
      vector<llvm::json::Object> projected_uses;
      projected_uses.reserve(end - begin);
      for (size_t index = begin; index < end; ++index) {
        const UseFact& fact = facts[index];
        projected_uses.push_back(llvm::json::Object{
            {"location", LocationObject(fact.location)},
            {"requirement", fact.use->is_full_use()
                                ? "full" : "forward_declaration"},
            {"origin", "unknown"},
            {"condition", ConditionObject(fact.condition)},
            {"macro", UnavailableValue()}});
      }
      const UseFact& first = facts[begin];
      const UseFact* declaration_fact = nullptr;
      for (size_t index = begin; index < end; ++index) {
        if (facts[index].has_qualified_name) {
          declaration_fact = &facts[index];
          break;
        }
      }
      llvm::json::Object symbol;
      symbol["name"] = first.name;
      symbol["qualified_name"] = declaration_fact
          ? KnownString(declaration_fact->qualified_name) : UnavailableValue();
      symbol["declaration"] = declaration_fact
          ? AvailableLocation(
                GlobalFlags().output_schema_version >= 2
                    ? GetLocation(StructuredDeclarationIdentity(
                          declaration_fact->use->decl()))
                    : declaration_fact->use->decl_loc(),
                false)
          : UnavailableValue();
      if (GlobalFlags().output_schema_version >= 2) {
        const string declaration_kind = declaration_fact
            ? StructuredDeclarationKind(declaration_fact->use->decl())
            : string();
        symbol["declaration_kind"] = declaration_kind.empty()
            ? UnavailableValue() : KnownString(declaration_kind);
      }
      symbol["uses"] = internal::SortAndDeduplicateProjectedUses(
          std::move(projected_uses));
      symbols.push_back(std::move(symbol));
      begin = end;
    }
    return symbols;
  };

  const auto operation_provider = [&](const OperationRef& operation) {
    llvm::json::Object provider;
    if (operation.reason_code == "semantic_definition_provider") {
      const auto found = semantic_definition_provider_facts_.find(
          operation.line->start_linenum());
      if (found == semantic_definition_provider_facts_.end() ||
          found->second.empty()) {
        provider["selected"] = UnavailableValue();
        provider["candidates"] = UnavailableValue();
        provider["chain"] = UnavailableValue();
        return provider;
      }
      const string selected_spelling = operation.line->quoted_include();
      const string selected_path = NormalizeFilePath(
          GetFilePath(operation.line->included_file()));
      llvm::json::Object selected{
          {"spelling", selected_spelling},
          {"path", selected_path}};
      llvm::json::Array candidates;
      candidates.push_back(llvm::json::Object{
          {"spelling", selected_spelling},
          {"path", selected_path}});
      llvm::json::Array chain;
      chain.push_back(file_path());
      chain.push_back(selected_path);
      provider["selected"] = llvm::json::Object{
          {"status", "known"}, {"value", std::move(selected)}};
      provider["candidates"] = llvm::json::Object{
          {"status", "known"}, {"value", std::move(candidates)}};
      provider["chain"] = llvm::json::Object{
          {"status", "known"}, {"value", std::move(chain)}};
      return provider;
    }
    if (operation.reason_code == "macro_body_dependency_unavailable") {
      provider["selected"] = UnavailableValue();
      provider["candidates"] = UnavailableValue();
      provider["chain"] = UnavailableValue();
      return provider;
    }
    if (operation.reason_code == "macro_body_dependency") {
      const auto found = macro_body_dependency_facts_.find(
          operation.line->included_file());
      if (found == macro_body_dependency_facts_.end() ||
          found->second.empty()) {
        provider["selected"] = UnavailableValue();
        provider["candidates"] = UnavailableValue();
        provider["chain"] = UnavailableValue();
        return provider;
      }
      const string path = NormalizeFilePath(
          GetFilePath(operation.line->included_file()));
      llvm::json::Object selected{
          {"spelling", operation.line->quoted_include()}, {"path", path}};
      llvm::json::Array candidates;
      candidates.push_back(llvm::json::Object{
          {"spelling", operation.line->quoted_include()}, {"path", path}});
      llvm::json::Array chain;
      chain.push_back(file_path());
      chain.push_back(path);
      provider["selected"] = llvm::json::Object{
          {"status", "known"}, {"value", std::move(selected)}};
      provider["candidates"] = llvm::json::Object{
          {"status", "known"}, {"value", std::move(candidates)}};
      provider["chain"] = llvm::json::Object{
          {"status", "known"}, {"value", std::move(chain)}};
      return provider;
    }
    if (operation.subject_action != "add" ||
        operation.item_kind != "include") {
      provider["selected"] = NotApplicableValue();
      provider["candidates"] = NotApplicableValue();
      provider["chain"] = NotApplicableValue();
      return provider;
    }

    provider["selected"] = UnavailableValue();
    provider["candidates"] = UnavailableValue();
    provider["chain"] = UnavailableValue();

    vector<const OneUse*> motivating_uses;
    for (const OneUse& use : symbol_uses_) {
      if (!use.ignore_use() && use.is_full_use() &&
          use.has_suggested_header() &&
          use.suggested_header() == operation.line->quoted_include()) {
        motivating_uses.push_back(&use);
      }
    }
    if (motivating_uses.empty())
      return provider;

    struct ProviderRef {
      string path;
      string spelling;
    };
    const auto ref_less = [](const ProviderRef& lhs, const ProviderRef& rhs) {
      return std::tie(lhs.path, lhs.spelling) <
             std::tie(rhs.path, rhs.spelling);
    };
    const auto ref_equal = [](const ProviderRef& lhs, const ProviderRef& rhs) {
      return lhs.path == rhs.path && lhs.spelling == rhs.spelling;
    };

    vector<ProviderRef> refs;
    set<string> selected_paths;
    map<string, string> path_by_spelling;
    bool candidates_available = true;
    bool selected_available = true;
    bool saw_candidate = false;
    bool saw_selected = false;
    for (const OneUse* use : motivating_uses) {
      const vector<string>& spellings = use->computed_public_headers();
      const vector<string>& paths = use->computed_public_header_paths();
      if (spellings.size() != paths.size()) {
        candidates_available = false;
        selected_available = false;
        continue;
      }
      for (size_t index = 0; index < spellings.size(); ++index) {
        saw_candidate = true;
        const bool is_selected =
            spellings[index] == operation.line->quoted_include();
        saw_selected = saw_selected || is_selected;
        const string resolved_path =
            preprocessor_info_->ResolveIncludePath(file_, spellings[index]);
        const string advertised_path = paths[index].empty()
            ? string()
            : NormalizeFilePath(MakeAbsolutePath(paths[index]));
        const bool context_matches =
            !resolved_path.empty() &&
            (advertised_path.empty() || advertised_path == resolved_path) &&
            preprocessor_info_->PathIsOnIncludeAncestry(
                use->decl_loc(), resolved_path);
        if (!context_matches) {
          candidates_available = false;
          if (is_selected)
            selected_available = false;
          continue;
        }

        const ProviderRef ref = {resolved_path, spellings[index]};
        const auto existing = path_by_spelling.find(ref.spelling);
        if (existing != path_by_spelling.end() &&
            existing->second != ref.path) {
          candidates_available = false;
          if (is_selected)
            selected_available = false;
        } else {
          path_by_spelling[ref.spelling] = ref.path;
        }
        refs.push_back(ref);
        if (is_selected)
          selected_paths.insert(ref.path);
      }
    }

    if (!saw_selected || !selected_available || selected_paths.size() != 1)
      return provider;
    const string selected_path = *selected_paths.begin();
    provider["selected"] = llvm::json::Object{
        {"status", "known"},
        {"value", llvm::json::Object{
            {"spelling", operation.line->quoted_include()},
            {"path", selected_path}}}};

    vector<string> common_chain;
    bool chain_available = true;
    for (const OneUse* use : motivating_uses) {
      vector<string> chain = preprocessor_info_->IncludeChainForUse(
          use->use_loc(), use->decl_loc(), selected_path);
      if (chain.empty()) {
        chain_available = false;
        break;
      }
      if (common_chain.empty()) {
        common_chain = std::move(chain);
      } else if (common_chain != chain) {
        chain_available = false;
        break;
      }
    }
    if (chain_available && !common_chain.empty()) {
      // Records use a worktree-relative analyzed-file identity while provider
      // references are absolute. Preserve both schema identities at the ends.
      common_chain.front() = file_path();
      llvm::json::Array chain_value;
      for (const string& path : common_chain)
        chain_value.push_back(path);
      provider["chain"] = llvm::json::Object{
          {"status", "known"}, {"value", std::move(chain_value)}};
    }

    if (!saw_candidate || !candidates_available)
      return provider;

    std::sort(refs.begin(), refs.end(), ref_less);
    refs.erase(std::unique(refs.begin(), refs.end(), ref_equal), refs.end());
    llvm::json::Array candidates;
    for (const ProviderRef& ref : refs) {
      candidates.push_back(llvm::json::Object{
          {"spelling", ref.spelling}, {"path", ref.path}});
    }
    provider["candidates"] = llvm::json::Object{
        {"status", "known"}, {"value", std::move(candidates)}};
    return provider;
  };

  std::sort(operations.begin(), operations.end(),
            [&](const OperationRef& lhs, const OperationRef& rhs) {
              return operation_key(lhs) < operation_key(rhs);
            });

  llvm::json::Array operation_values;
  int operation_number = 1;
  for (const OperationRef& operation : operations) {
    llvm::json::Object provider = operation_provider(operation);
    const IwyuPreprocessorInfo::ActiveCondition edit_condition =
        operation_condition(operation);

    llvm::json::Object value;
    value["id"] = "op-" + to_string(operation_number++);
    value["subject_action"] = operation.subject_action;
    value["disposition"] = operation.disposition;
    value["item_kind"] = operation.item_kind;
    value["spelling"] = operation_spelling(operation);
    if (operation.reason_code == "semantic_definition_provider") {
      const auto found = semantic_definition_provider_facts_.find(
          operation.line->start_linenum());
      StructuredLocationValue start;
      if (found != semantic_definition_provider_facts_.end() &&
          !found->second.empty() &&
          StructuredLocation(found->second.begin()->second.include_location,
                             false, &start)) {
        StructuredLocationValue end = start;
        end.column += operation.line->quoted_include().size();
        value["source_range"] = llvm::json::Object{
            {"status", "known"},
            {"value", llvm::json::Object{
                {"start", LocationObject(start)},
                {"end", LocationObject(end)}}}};
      } else {
        value["source_range"] = UnavailableValue();
      }
    } else if (operation.subject_action == "add") {
      value["source_range"] = NotApplicableValue();
    } else if (GlobalFlags().output_schema_version >= 2 &&
               operation.line->is_present() &&
               operation.line->start_linenum() > 0 &&
               operation.line->end_linenum() >=
                   operation.line->start_linenum()) {
      const clang::SourceManager& source_manager = *GlobalSourceManager();
      const clang::FileID file_id = source_manager.translateFile(file_);
      const SourceLocation end_line = source_manager.translateLineCol(
          file_id, operation.line->end_linenum(), 1);
      bool invalid = false;
      const llvm::StringRef end_text = end_line.isValid()
          ? GetSourceTextUntilEndOfLine(end_line, DefaultDataGetter())
          : llvm::StringRef();
      if (end_line.isValid()) {
        source_manager.getCharacterData(end_line, &invalid);
      } else {
        invalid = true;
      }
      if (!invalid) {
        StructuredLocationValue start{
            file_path(), operation.line->start_linenum(), 1};
        StructuredLocationValue end{
            file_path(), operation.line->end_linenum(),
            static_cast<int>(end_text.size()) + 1};
        value["source_range"] = llvm::json::Object{
            {"status", "known"},
            {"value", llvm::json::Object{
                {"start", LocationObject(start)},
                {"end", LocationObject(end)}}}};
      } else {
        value["source_range"] = UnavailableValue();
      }
    } else {
      value["source_range"] = UnavailableValue();
    }
    value["reason_code"] = operation.reason_code;
    value["reason"] = operation.reason;
    value["symbols"] = operation_symbols(operation);
    value["provider"] = std::move(provider);
    value["edit_condition"] = ConditionObject(edit_condition);
    if (operation.line->has_project_policy_decision()) {
      value["policy"] = llvm::json::Object{
          {"status", "known"},
          {"value", llvm::json::Object{
              {"policy_file", operation.line->decision_policy_file()},
              {"rule_id", operation.line->decision_policy_rule_id()},
              {"decision", operation.line->decision_policy_decision()}}}};
    } else {
      value["policy"] = UnavailableValue();
    }
    const string& configuration = GlobalFlags().configuration;
    if (configuration.empty()) {
      value["multi_config"] = NotApplicableValue();
    } else {
      llvm::json::Array contributions;
      contributions.push_back(llvm::json::Object{
          {"configuration", configuration},
          {"subject_action", operation.subject_action},
          {"disposition", operation.disposition},
          {"condition", ConditionObject(edit_condition)}});
      value["multi_config"] = llvm::json::Object{
          {"status", "known"}, {"value", std::move(contributions)}};
    }
    operation_values.push_back(std::move(value));
  }

  vector<const OneIncludeOrForwardDeclareLine*> desired_lines;
  if (!has_unrepresentable_duplicate) {
    for (const OneIncludeOrForwardDeclareLine& line : lines_) {
      if (line.is_desired() && !line.is_elaborated_type())
        desired_lines.push_back(&line);
    }
    const set<string> associated = AssociatedQuotedIncludes();
    std::sort(desired_lines.begin(), desired_lines.end(),
              [&](const OneIncludeOrForwardDeclareLine* lhs,
                  const OneIncludeOrForwardDeclareLine* rhs) {
                const IwyuFileInfo* lhs_info = lhs->IsIncludeLine()
                    ? preprocessor_info_->FileInfoFor(lhs->included_file())
                    : nullptr;
                const IwyuFileInfo* rhs_info = rhs->IsIncludeLine()
                    ? preprocessor_info_->FileInfoFor(rhs->included_file())
                    : nullptr;
                return internal::GetSortKey(*lhs, associated, lhs_info) <
                       internal::GetSortKey(*rhs, associated, rhs_info);
              });
  }

  const auto desired_item_spelling = [](const OneIncludeOrForwardDeclareLine& line) {
    if (GlobalFlags().output_schema_version >= 2)
      return line.line();
    const bool retained_by_policy =
        line.is_present() && line.has_structured_decision() &&
        line.decision_disposition() != "apply";
    return (!line.IsIncludeLine() && line.is_present()) || retained_by_policy
        ? line.source_spelling() : line.line();
  };

  // Schema v2 desired state identifies logical items, not physical occurrences.
  // Schema v1 retains its historical exact-spelling identity.
  set<pair<string, string>> desired_keys;
  desired_lines.erase(
      std::remove_if(
          desired_lines.begin(), desired_lines.end(),
          [&](const OneIncludeOrForwardDeclareLine* line) {
            return !desired_keys.insert(
                {item_kind(*line), desired_item_spelling(*line)}).second;
          }),
      desired_lines.end());

  llvm::json::Array desired_items;
  for (const OneIncludeOrForwardDeclareLine* line : desired_lines) {
    llvm::json::Array symbols;
    for (const auto& symbol_count : line->symbol_counts()) {
      if (!symbol_count.first.empty())
        symbols.push_back(symbol_count.first);
    }
    llvm::json::Object item;
    item["item_kind"] = item_kind(*line);
    item["spelling"] = desired_item_spelling(*line);
    item["symbols"] = std::move(symbols);
    desired_items.push_back(std::move(item));
  }

  vector<AssociatedHeader> sorted_associations = associated_header_details_;
  std::sort(sorted_associations.begin(), sorted_associations.end(),
            [](const AssociatedHeader& lhs, const AssociatedHeader& rhs) {
              return std::make_tuple(
                         NormalizeFilePath(GetFilePath(lhs.file_info->file_)),
                         lhs.relationship, lhs.reason_code) <
                     std::make_tuple(
                         NormalizeFilePath(GetFilePath(rhs.file_info->file_)),
                         rhs.relationship, rhs.reason_code);
            });
  llvm::json::Array associations;
  for (const AssociatedHeader& association : sorted_associations) {
    associations.push_back(llvm::json::Object{
        {"path", NormalizeFilePath(GetFilePath(association.file_info->file_))},
        {"relationship", association.relationship},
        {"reason_code", association.reason_code},
        {"reason", association.reason}});
  }

  llvm::json::Array conditional_regions;
  vector<IwyuPreprocessorInfo::ConditionalRegion> region_facts =
      preprocessor_info_->ConditionalRegionsFor(file_);
  std::sort(region_facts.begin(), region_facts.end(),
            [](const auto& lhs, const auto& rhs) {
              return std::tie(lhs.opening_offset, lhs.branch_index,
                              lhs.directive_offset, lhs.file_id) <
                     std::tie(rhs.opening_offset, rhs.branch_index,
                              rhs.directive_offset, rhs.file_id);
            });
  for (const auto& region : region_facts) {
    const char* activity =
        region.activity == IwyuPreprocessorInfo::BranchActivity::kActive
            ? "active"
            : (region.activity ==
                       IwyuPreprocessorInfo::BranchActivity::kInactive
                   ? "inactive" : "unknown");
    const char* lexical_scope =
        region.lexical_scope == IwyuPreprocessorInfo::LexicalScope::kFile
            ? "file"
            : (region.lexical_scope ==
                       IwyuPreprocessorInfo::LexicalScope::kNonFile
                   ? "non_file" : "unknown");
    llvm::json::Object body_end = region.has_body_end
        ? llvm::json::Object{{"status", "known"},
                             {"value", region.body_end_offset}}
        : llvm::json::Object{{"status", "unknown"}, {"value", nullptr}};
    llvm::json::Object parent = region.parent_source_region_id.empty()
        ? NotApplicableValue()
        : KnownString(region.parent_source_region_id);
    conditional_regions.push_back(llvm::json::Object{
        {"source_region_id", region.source_region_id},
        {"file_id", region.file_id},
        {"opening_offset", region.opening_offset},
        {"directive_offset", region.directive_offset},
        {"body_begin_offset", region.body_begin_offset},
        {"body_end_offset", std::move(body_end)},
        {"branch_index", region.branch_index},
        {"parent_source_region_id", std::move(parent)},
        {"directive_kind", region.directive_kind},
        {"expression", region.expression},
        {"activity", activity},
        {"lexical_scope", lexical_scope},
        {"opening_location", AvailableLocation(region.opening_location, false)},
        {"directive_location", AvailableLocation(region.directive_location, false)}});
  }

  llvm::json::Array diagnostics;
  const string absolute_file_path =
      NormalizeFilePath(MakeAbsolutePath(file_path()));
  for (const GlobalStructuredDiagnostic& diagnostic :
       GlobalStructuredDiagnostics()) {
    if (NormalizeFilePath(MakeAbsolutePath(diagnostic.file_path)) !=
        absolute_file_path) {
      continue;
    }
    diagnostics.push_back(llvm::json::Object{
        {"severity", "warning"},
        {"code", diagnostic.code},
        {"message", diagnostic.message},
        {"location", UnavailableValue()}});
  }
  for (const StructuredDiagnostic& diagnostic : structured_diagnostics_) {
    diagnostics.push_back(llvm::json::Object{
        {"severity", diagnostic.severity},
        {"code", diagnostic.code},
        {"message", diagnostic.message},
        {"location", AvailableLocation(diagnostic.location, false)}});
  }
  for (const OneUse& use : symbol_uses_) {
    if (!use.is_iwyu_violation())
      continue;
    diagnostics.push_back(llvm::json::Object{
        {"severity", "warning"},
        {"code", use.is_full_use() ? "missing_direct_include"
                                    : "missing_declaration"},
        {"message", GetWarningDescription(use)},
        {"location", AvailableLocation(use.use_loc(), true)}});
  }
  if (has_unrepresentable_duplicate) {
    diagnostics.push_back(llvm::json::Object{
        {"severity", "warning"},
        {"code", "duplicate_occurrence_unrepresentable"},
        {"message", "Duplicate item occurrences cannot be represented "
                    "unambiguously by output schema version " +
                    to_string(GlobalFlags().output_schema_version) + "."},
        {"location", UnavailableValue()}});
  }

  llvm::json::Object git_revision;
  const string revision = IWYU_GIT_REV;
  if (revision.empty()) {
    git_revision = UnavailableValue();
  } else {
    git_revision = llvm::json::Object{{"status", "known"},
                                      {"value", revision}};
  }

  const CommandlineFlags& flags = GlobalFlags();
  llvm::json::Object tool;
  tool["name"] = "include-what-you-use";
  tool["version"] = IWYU_VERSION_STRING;
  tool["git_revision"] = std::move(git_revision);
  tool["clang_version"] = CLANG_VERSION_STRING;

  llvm::json::Object invocation;
  invocation["id"] = flags.invocation_id;
  invocation["index"] = flags.invocation_index;
  invocation["translation_unit"] = flags.translation_unit;
  invocation["configuration"] = flags.has_configuration
      ? KnownString(flags.configuration) : UnavailableValue();
  invocation["command_digest"] = flags.command_digest;

  llvm::json::Object file;
  file["path"] = file_path();
  file["kind"] = ProjectPolicyFileKind(file_path());

  llvm::json::Object file_digest = UnavailableValue();
  const clang::FileID digest_file_id = GlobalSourceManager()->translateFile(file_);
  if (digest_file_id.isValid()) {
    bool digest_invalid = false;
    const llvm::StringRef source_bytes =
        GlobalSourceManager()->getBufferData(digest_file_id, &digest_invalid);
    if (!digest_invalid) {
      const auto bytes = llvm::ArrayRef<uint8_t>(
          reinterpret_cast<const uint8_t*>(source_bytes.data()),
          source_bytes.size());
      file_digest = KnownString(
          "sha256:" + llvm::toHex(llvm::SHA256::hash(bytes), true));
    }
  }

  llvm::json::Object desired_state;
  if (has_unrepresentable_duplicate) {
    desired_state = UnavailableDesiredState();
  } else {
    desired_state["status"] = "known";
    desired_state["complete"] = true;
    desired_state["items"] = std::move(desired_items);
  }

  llvm::json::Array declaration_facts;
  if (flags.output_schema_version >= 2) {
    struct DeclarationFact {
      string qualified_name;
      string kind;
      StructuredLocationValue declaration;
    };
    vector<DeclarationFact> facts;
    for (const OneUse& use : symbol_uses_) {
      if (!HasStableDeclarationIdentity(use))
        continue;
      const NamedDecl* identity = StructuredDeclarationIdentity(use.decl());
      const string kind = StructuredDeclarationKind(identity);
      if (kind.empty())
        continue;
      StructuredLocationValue declaration;
      if (!StructuredLocation(GetLocation(identity), false, &declaration))
        continue;
      facts.push_back({internal::GetQualifiedNameAsString(identity), kind,
                       declaration});
    }
    std::sort(facts.begin(), facts.end(), [](const DeclarationFact& lhs,
                                             const DeclarationFact& rhs) {
      return std::tie(lhs.qualified_name, lhs.kind, lhs.declaration.path,
                      lhs.declaration.line, lhs.declaration.column) <
             std::tie(rhs.qualified_name, rhs.kind, rhs.declaration.path,
                      rhs.declaration.line, rhs.declaration.column);
    });
    facts.erase(std::unique(
        facts.begin(), facts.end(), [](const DeclarationFact& lhs,
                                      const DeclarationFact& rhs) {
          return lhs.qualified_name == rhs.qualified_name &&
                 lhs.kind == rhs.kind;
        }), facts.end());
    for (const DeclarationFact& fact : facts) {
      declaration_facts.push_back(llvm::json::Object{
          {"qualified_name", fact.qualified_name},
          {"kind", fact.kind},
          {"declaration", LocationObject(fact.declaration)}});
    }
  }

  llvm::json::Object record;
  record["schema_version"] = flags.output_schema_version;
  record["record_type"] = "file_analysis";
  record["tool"] = std::move(tool);
  record["invocation"] = std::move(invocation);
  record["file"] = std::move(file);
  const bool has_applied_edit = std::any_of(
      operations.begin(), operations.end(), [](const OperationRef& operation) {
        return operation.disposition == "apply" &&
            (operation.subject_action == "add" ||
             operation.subject_action == "remove");
      });
  record["status"] = has_unrepresentable_duplicate
                         ? "skipped"
                         : (has_applied_edit ? "changes" : "correct");
  record["associated_headers"] = std::move(associations);
  if (flags.has_configuration) {
    llvm::json::Array configurations;
    configurations.push_back(flags.configuration);
    record["analyzed_configurations"] = llvm::json::Object{
        {"status", "known"}, {"value", std::move(configurations)}};
  } else {
    record["analyzed_configurations"] = UnavailableValue();
  }
  record["operations"] = std::move(operation_values);
  record["desired_state"] = std::move(desired_state);
  record["diagnostics"] = std::move(diagnostics);
  if (flags.output_schema_version >= 2) {
    record["source_identity"] = StructuredSourceIdentity(file_);
    record["source_digest"] = StructuredSourceDigest(file_);
    record["file_digest"] = std::move(file_digest);
    record["conditional_regions"] = std::move(conditional_regions);
    record["declaration_facts"] = std::move(declaration_facts);
    record["declaration_conflicts"] = llvm::json::Array{};
  }

  llvm::outs() << llvm::json::Value(std::move(record)) << '\n';
}

size_t IwyuFileInfo::CalculateAndReportIwyuViolations() {
  // This is used to calculate our own desired includes.  That depends
  // on what our associated files' desired includes are: if we use
  // bar.h and foo.h is adding it, we don't need to add it ourself.
  // On the other hand, if foo.h used to have it but is removing it,
  // we *do* need to add it.
  set<string> associated_desired_includes = AssociatedDesiredIncludes();

  CalculateIwyuViolations(&symbol_uses_);
  if (GlobalFlags().output_format == CommandlineFlags::kTextOutput)
    EmitWarningMessages(symbol_uses_);
  internal::CalculateDesiredIncludesAndForwardDeclares(
      symbol_uses_, associated_desired_includes, kept_includes_,  &lines_);

  // SEM-004 requirements are occurrence-specific. Re-assert only the exact
  // directive lines selected from concrete FileID ancestry after ordinary
  // spelling-based duplicate pruning has run. Legacy text has no structured
  // keep operation, but restoring the desired line keeps fix_includes from
  // deleting the selected body provider.
  for (OneIncludeOrForwardDeclareLine& line : lines_) {
    if (!line.IsIncludeLine() || !line.is_present() ||
        !ContainsKey(semantic_definition_provider_lines_,
                     line.start_linenum())) {
      continue;
    }
    line.set_desired();
    if (GlobalFlags().output_format == CommandlineFlags::kJsonOutput) {
      const auto facts = semantic_definition_provider_facts_.find(
          line.start_linenum());
      if (facts != semantic_definition_provider_facts_.end()) {
        for (const auto& fact : facts->second)
          line.AddSymbolUse(
              fact.second.function->getQualifiedNameAsString());
      }
      line.SetStructuredDecision(
          "keep", "keep", "semantic_definition_provider",
          "This exact entered include occurrence supplies an evaluated "
          "inline or template definition obligation.");
    }
  }

  // Keep decisions are operations even though the protected item is present in
  // both the input and desired state. Preserve the recommendation that policy
  // overrode instead of losing it when desired membership is restored.
  for (OneIncludeOrForwardDeclareLine& line : lines_) {
    const bool has_symbol_motivation = std::any_of(
        line.symbol_counts().begin(), line.symbol_counts().end(),
        [](const auto& symbol_count) { return !symbol_count.first.empty(); });
    if (!line.IsIncludeLine() || !line.is_present() ||
        (!line.was_restored_by_keep() && has_symbol_motivation))
      continue;
    const auto reason = kept_include_reasons_.find(line.included_file());
    if (reason != kept_include_reasons_.end()) {
      line.SetStructuredDecision("remove", "keep", reason->second.reason_code,
                                 reason->second.reason);
    }
  }

  for (OneIncludeOrForwardDeclareLine& line : lines_) {
    if (line.IsIncludeLine() || !line.is_present() || !line.is_desired() ||
        !line.has_forward_declare_keep_reason() ||
        line.was_desired_by_use()) {
      continue;
    }
    line.SetStructuredDecision(
        "remove", "keep", line.forward_declare_keep_reason_code(),
        line.forward_declare_keep_reason());
  }

  // Remove desired inclusions that have been inhibited by pragma
  // "no_include".
  for (OneIncludeOrForwardDeclareLine& line : lines_) {
    if (line.IsIncludeLine() && line.is_desired() &&
        preprocessor_info_->IncludeIsInhibited(file_, line.quoted_include())) {
      if (line.is_present()) {
        line.SetStructuredDecision(
            "remove", "apply", "pragma_no_include_remove",
            "IWYU pragma no_include excludes this present include from the "
            "desired state.");
      } else {
        line.SetStructuredDecision(
            "add", "suppress", "pragma_no_include",
            "IWYU pragma no_include suppresses this proposed addition.");
      }
      line.clear_desired();
    }
  }

  internal::CleanupPrefixHeaderIncludes(preprocessor_info_, &lines_);

  size_t project_policy_violations = 0;
  const ProjectPolicy* project_policy = GlobalProjectPolicy();
  if (project_policy) {
    const auto addition_condition =
        [&](const OneIncludeOrForwardDeclareLine& line) {
          using ActiveCondition = IwyuPreprocessorInfo::ActiveCondition;
          bool found = false;
          ActiveCondition common;
          for (const OneUse& use : symbol_uses_) {
            if (use.ignore_use() || !use.is_full_use() ||
                !use.has_suggested_header() ||
                use.suggested_header() != line.quoted_include()) {
              continue;
            }
            const ActiveCondition condition =
                preprocessor_info_->ActiveConditionAt(use.use_loc());
            if (!found) {
              common = condition;
              found = true;
              continue;
            }
            if (common.status != condition.status ||
                common.expression != condition.expression ||
                common.directive_location != condition.directive_location ||
                common.active_branch != condition.active_branch ||
                common.source_region_id != condition.source_region_id ||
                common.source_region_path != condition.source_region_path) {
              return ActiveCondition{};
            }
          }
          return found ? common : ActiveCondition{};
        };

    for (OneIncludeOrForwardDeclareLine& line : lines_) {
      if (!line.IsIncludeLine() || line.is_present() || !line.is_desired() ||
          line.has_structured_decision()) {
        continue;
      }

      string includee = line.quoted_include();
      if (includee.size() >= 2 &&
          ((includee.front() == '"' && includee.back() == '"') ||
           (includee.front() == '<' && includee.back() == '>'))) {
        includee = includee.substr(1, includee.size() - 2);
      }
      includee = NormalizeFilePath(includee);

      ProjectPolicyMatchContext context;
      context.includer = file_path();
      context.includee = includee;
      context.file_kind = ProjectPolicyFileKind(context.includer);
      const IwyuPreprocessorInfo::ActiveCondition condition =
          addition_condition(line);
      context.active_condition = PolicyActiveCondition(condition);
      context.edit_lexical_scope =
          PolicyEditLexicalScope(preprocessor_info_, file_, condition);
      if (GlobalFlags().has_configuration)
        context.configuration = GlobalFlags().configuration;

      SourceLocation diagnostic_location;
      for (const OneUse& use : symbol_uses_) {
        if (use.ignore_use() || !use.is_full_use() ||
            !use.has_suggested_header() ||
            use.suggested_header() != line.quoted_include()) {
          continue;
        }
        context.symbols.push_back(use.symbol_name());
        if (diagnostic_location.isInvalid())
          diagnostic_location = use.use_loc();
      }
      std::sort(context.symbols.begin(), context.symbols.end());
      context.symbols.erase(
          std::unique(context.symbols.begin(), context.symbols.end()),
          context.symbols.end());

      const ProjectPolicyMatchResult match =
          project_policy->FirstMatchingRule(ProjectPolicyActionFamily::Addition,
                                            context);
      if (match.kind == ProjectPolicyMatchResult::Kind::NoMatch)
        continue;
      if (match.kind == ProjectPolicyMatchResult::Kind::Indeterminate) {
        const string reason_code = "project_policy_" +
            match.indeterminate_fact + "_unknown";
        const string reason =
            "Project policy '" + match.indeterminate_policy_file +
            "' rule '" + match.indeterminate_rule_id +
            "' could not determine whether " + match.indeterminate_fact +
            " matches while evaluating addition of " +
            line.quoted_include() + " to " + context.includer +
            "; the addition is suppressed.";
        line.SetStructuredDecision("add", "suppress", reason_code, reason);
        line.clear_desired();
        AddStructuredDiagnostic(diagnostic_location, reason_code, reason);
        if (GlobalFlags().output_format == CommandlineFlags::kTextOutput) {
          errs() << context.includer << ": warning: " << reason_code << ": "
                 << reason << '\n';
        }
        ++project_policy_violations;
        continue;
      }

      const ProjectPolicyDecision& decision = *match.decision;
      if (decision.action == ProjectPolicyAction::AllowAdd) {
        const string reason = "Project policy '" + decision.policy_file +
            "' rule '" + decision.rule_id + "' allows adding " +
            line.quoted_include() + " to " + context.includer + ".";
        line.SetProjectPolicyDecision(
            "add", "apply", "project_policy_allow_add", reason,
            decision.policy_file, decision.rule_id, "allow");
        continue;
      }

      const string reason = "Project policy '" + decision.policy_file +
          "' rule '" + decision.rule_id + "' denies adding " +
          line.quoted_include() + " to " + context.includer + ".";
      line.SetProjectPolicyDecision(
          "add", "suppress", "project_policy_deny_add", reason,
          decision.policy_file, decision.rule_id, "deny");
      line.clear_desired();
      AddStructuredDiagnostic(diagnostic_location,
                              "project_policy_deny_add", reason);
      if (GlobalFlags().output_format == CommandlineFlags::kTextOutput) {
        errs() << context.includer
               << ": warning: project_policy_deny_add: " << reason << '\n';
      }
      ++project_policy_violations;
    }

    for (OneIncludeOrForwardDeclareLine& line : lines_) {
      if (!line.IsIncludeLine() || !line.is_present() || line.is_desired() ||
          line.has_structured_decision()) {
        continue;
      }

      string includee = line.quoted_include();
      if (includee.size() >= 2 &&
          ((includee.front() == '"' && includee.back() == '"') ||
           (includee.front() == '<' && includee.back() == '>'))) {
        includee = includee.substr(1, includee.size() - 2);
      }
      includee = NormalizeFilePath(includee);

      ProjectPolicyMatchContext context;
      context.includer = file_path();
      context.includee = includee;
      context.file_kind = ProjectPolicyFileKind(context.includer);
      const IwyuPreprocessorInfo::ActiveCondition condition =
          preprocessor_info_->DirectIncludeConditionAt(
              file_, line.quoted_include(), line.start_linenum());
      context.active_condition = PolicyActiveCondition(condition);
      context.edit_lexical_scope =
          PolicyEditLexicalScope(preprocessor_info_, file_, condition);
      if (GlobalFlags().has_configuration)
        context.configuration = GlobalFlags().configuration;

      const ProjectPolicyMatchResult match =
          project_policy->FirstMatchingRule(ProjectPolicyActionFamily::Removal,
                                            context);
      if (match.kind == ProjectPolicyMatchResult::Kind::NoMatch)
        continue;
      if (match.kind == ProjectPolicyMatchResult::Kind::Indeterminate) {
        const string reason_code = "project_policy_" +
            match.indeterminate_fact + "_unknown";
        const string reason =
            "Project policy '" + match.indeterminate_policy_file +
            "' rule '" + match.indeterminate_rule_id +
            "' could not determine whether " + match.indeterminate_fact +
            " matches while evaluating removal of " +
            line.quoted_include() + " from " + context.includer +
            "; the removal is suppressed.";
        line.SetStructuredDecision(
            "remove", "suppress", reason_code, reason);
        line.set_desired();
        AddStructuredDiagnostic(SourceLocation(), reason_code, reason);
        if (GlobalFlags().output_format == CommandlineFlags::kTextOutput) {
          errs() << context.includer << ": warning: " << reason_code << ": "
                 << reason << '\n';
        }
        ++project_policy_violations;
        continue;
      }

      const ProjectPolicyDecision& decision = *match.decision;
      const bool denied =
          decision.action == ProjectPolicyAction::DenyRemove;
      const string reason = "Project policy '" + decision.policy_file +
          "' rule '" + decision.rule_id + "' " +
          (denied ? "denies removing " : "keeps ") +
          line.quoted_include() + (denied ? " from " : " in ") +
          context.includer + ".";
      line.SetProjectPolicyDecision(
          "remove", denied ? "suppress" : "keep",
          denied ? "project_policy_deny_remove" : "project_policy_keep",
          reason, decision.policy_file, decision.rule_id,
          denied ? "deny" : "keep");
      line.set_desired();
      if (denied) {
        AddStructuredDiagnostic(SourceLocation(),
                                "project_policy_deny_remove", reason);
        if (GlobalFlags().output_format == CommandlineFlags::kTextOutput) {
          errs() << context.includer
                 << ": warning: project_policy_deny_remove: " << reason
                 << '\n';
        }
        ++project_policy_violations;
      }
    }
  }

  set<string> printable_associated_includes = AssociatedQuotedIncludes();
  for (const OneIncludeOrForwardDeclareLine& desired : lines_) {
    if (!desired.IsIncludeLine() || !desired.is_desired())
      continue;
    for (const OneIncludeOrForwardDeclareLine& removed : lines_) {
      if (!removed.IsIncludeLine() || removed.is_desired())
        continue;
      const vector<string> pair = {removed.quoted_include(),
                                   desired.quoted_include()};
      IncludePicker::VerifiedDottedInlineExport inline_export;
      if (GlobalIncludePicker().GetVerifiedDottedInlineExport(
              pair, GetFilePath(file_), &inline_export) &&
          removed.quoted_include() == inline_export.plain_spelling &&
          desired.quoted_include() == inline_export.inline_spelling) {
        printable_associated_includes.erase(desired.quoted_include());
      }
    }
  }

  string diff_output;
  internal::PrintableDiffs(
      GetFilePath(file_), preprocessor_info_, printable_associated_includes,
      lines_, &diff_output);
  if (GlobalFlags().output_format == CommandlineFlags::kTextOutput)
    errs() << diff_output;

  // Count semantic edits directly from the operation state. PrintableDiffs
  // deliberately gates parts of its rendering on verbosity, but --error and
  // project-policy accounting must not depend on which diff sections print.
  const size_t num_edits = std::count_if(
      lines_.begin(), lines_.end(),
      [](const OneIncludeOrForwardDeclareLine& line) {
        return line.is_present() != line.is_desired();
      });
  const size_t allowed_additions = std::count_if(
      lines_.begin(), lines_.end(),
      [](const OneIncludeOrForwardDeclareLine& line) {
        return !line.is_present() && line.is_desired() &&
               line.decision_reason_code() == "project_policy_allow_add";
      });
  CHECK_(allowed_additions <= num_edits);
  return num_edits - allowed_additions + project_policy_violations;
}

}  // namespace include_what_you_use
