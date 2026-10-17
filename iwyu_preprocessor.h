//===--- iwyu_preprocessor.h - handle #includes/#defines for iwyu ---------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

// The class that gets preprocessor callbacks: #includes, #defines,
// #pragmas, and the like.
//
// It has three main tasks in iwyu:
//
// 1) Record the #include hierarchy.  For each file it sees, it creates
// an IwyuOutput class for that file, which is set up to hold the direct
// includes that the file has.
//
// 2) Record iwyu violations for macro uses.  In particular, if a macro
// is #defined in one file and the token is accessed in another, do an
// iwyu check on that use.
//

// 3) Process iwyu pragma-like constructs.  Comments beginning "//"
// are allowed to follow any pragma, otherwise extraneous text on the
// line will result in an error message being logged. Here are the
// constructs we look for:
// Full-line constructs:
//    a) // IWYU pragma: private, include "foo/bar/baz.h"
//    b) // IWYU pragma: private
//    c) // IWYU pragma: begin_exports
//    d) // IWYU pragma: end_exports
//    e) // IWYU pragma: no_include "foo/bar/baz.h"
//    f) // IWYU pragma: no_forward_declare foo::Bar
//    g) // IWYU pragma: friend <regexp>
//       // IWYU pragma: friend "<regexp>" -- needed if spaces in regexp.
//    h) // IWYU pragma: begin_keep
//    i) // IWYU pragma: end_keep
// 'Annotation' constructs:
//    h) #include "foo/bar/baz.h"  // IWYU pragma: export
//    i) #include "foo/bar/baz.h"  // IWYU pragma: keep
//
// 4) Process doxygen @headername directives. In later versions of GCC,
//    these directives are like IWYU pragma private directives:
//    @headername{foo} means to include <foo> instead.
//    The arguments are allowed to be a comma-separated list.
//    See
//    http://gcc.gnu.org/onlinedocs/libstdc++/manual/documentation_hacking.html
//
// This class finishes its processing before the 'main' iwyu
// processing is done, so other iwyu consumers can access the main
// outputs of this class:
//    * The map from include-name to FileEntry*.
//    * The map from FileEntry* to its IwyuFileInfo object.
//    * TODO(csilvers): Information about direct includes of a FileEntry*
//    * The 'intends to provide' map, which encapsulates some
//      of the information about public vs private headers.
//    * Testing and reporting membership in the main compilation unit.

#ifndef INCLUDE_WHAT_YOU_USE_IWYU_PREPROCESSOR_H_
#define INCLUDE_WHAT_YOU_USE_IWYU_PREPROCESSOR_H_

#include <cstddef>                      // for size_t
#include <map>                          // for map
#include <set>                          // for set
#include <stack>                        // for stack
#include <string>                       // for string
#include <utility>                      // for pair
#include <vector>                       // for vector

#include "iwyu_output.h"
#include "iwyu_port.h"  // for CHECK_

#include "clang/Basic/SourceLocation.h"
#include "clang/Basic/SourceManager.h"
#include "clang/Lex/Preprocessor.h"
#include "clang/Lex/PPCallbacks.h"
#include "clang/Lex/Token.h"

namespace clang {
class ASTContext;
class FileEntry;
class IdentifierInfo;
class MacroInfo;
class NamedDecl;
}  // namespace clang

namespace include_what_you_use {

using std::map;
using std::set;
using std::stack;
using std::string;
using std::vector;
using std::multimap;

class IwyuPreprocessorInfo : public clang::PPCallbacks,
                             public clang::CommentHandler {
 public:
  explicit IwyuPreprocessorInfo(clang::Preprocessor& preprocessor)
      : preprocessor_(&preprocessor), expanded_brace_depth_(0),
        expanded_scope_unknown_(false), main_file_(nullptr),
        empty_file_info_(nullptr, this, "") {}

  // Observe Clang's final macro-expanded token stream. This supplies an early
  // non-file classification for braced scopes; the completed AST independently
  // rejects directives inside declarations, statements, types, or declarators.
  void ObserveExpandedToken(const clang::Token& token);
  void ClassifyConditionalRegionScopes(clang::ASTContext& context);

  // The client *must* call this from the beginning of HandleTranslationUnit()
  void HandlePreprocessingDone();

  // Resolve declaration references in every exported function-like macro body
  // after Clang has built the AST. Records belong to the defining include
  // instance, so later files cannot erase a header's self-containment facts.
  void ResolveMacroBodyDependencies(clang::ASTContext& context);

  // More direct ways of getting at this information
  const clang::FileEntry* main_file() const {
    return main_file_;
  }

  const set<const clang::FileEntry*>* files_to_report_iwyu_violations_for()
      const {
    return &files_to_report_iwyu_violations_for_;
  }

  // Given a quoted include like '<vector>', or '"ads/base.h"',
  // returns the FileEntry for that file, or nullptr if none is
  // found.  If multiple files are included under the same
  // quoted-include name (which can happen via #include-next),
  // one is returned arbitrarily.  (But always the same one.)
  const clang::FileEntry* IncludeToFileEntry(const string quoted_include) const;

  // Returns an IwyuFileInfo struct (from iwyu_output.h) corresponding
  // to the given file, or nullptr if no such struct can be found.
  // Note this is a const method that returns a non-const pointer.
  // Be careful if using this method in threaded contexts.
  IwyuFileInfo* FileInfoFor(const clang::FileEntry* file) const;

  // Instead of returning nullptr if no file info can be found, returns
  // an empty IwyuFileInfo struct.
  const IwyuFileInfo& FileInfoOrEmptyFor(const clang::FileEntry* file) const;

  // For every file we've seen (that is, that we've #included),
  // returns what files it 'intends' to provide full type information
  // for.  The motivation is that a file like <vector> #includes
  // <memory> and doesn't expect you to have to, even though
  // technically it's required whenever you create a vector<Foo>,
  // which is really vector<Foo, alloc<Foo>>.  We say you don't have
  // to #include <memory> because vector intends to provide the full
  // types from <memory> for you.
  //    The rule we use is every file intends to provide full type
  // information for the files it directly includes.  For public
  // header files -- ones that the include-picker can map another file
  // to -- we relax the rule to say the public header intends to
  // provide *every* header file behind it, direct or no.  This scheme
  // isn't perfect -- it says <map> intends to provide the full type
  // of pair<>, when really it just uses it internally -- but it's a
  // reasonable heuristic.
  //    Returns true iff our analysis shows that public_header intends
  // to provide all the symbols in other_file.
  bool PublicHeaderIntendsToProvide(const clang::FileEntry* public_header,
                                    const clang::FileEntry* other_file) const;

  // Returns true if the first file directly or indirectly includes
  // the second.
  bool FileTransitivelyIncludes(const clang::FileEntry* includer,
                                const clang::FileEntry* includee) const;
  bool FileTransitivelyIncludes(const clang::FileEntry* includer,
                                const string& quoted_includee) const;
  // This seems like a weird way to call this function, but we
  // happen to need this in iwyu_output.cc:
  bool FileTransitivelyIncludes(const string& quoted_includer,
                                const clang::FileEntry* includee) const;

  // Resolve one quoted include exactly as it would be resolved from includer.
  // Returns an empty path when the spelling is malformed or cannot be found.
  string ResolveIncludePath(const clang::FileEntry* includer,
                            const string& quoted_include) const;

  // Returns true only when one concrete declaration FileID ancestry contains
  // normalized_path. Physical FileEntry reachability is deliberately ignored.
  bool PathIsOnIncludeAncestry(
      clang::SourceLocation declaration_loc,
      const string& normalized_path) const;

  // Returns the concrete include-instance chain from use_loc to the selected
  // path on declaration_loc's FileID ancestry. The use FileID must itself be
  // an ancestor of that declaration instance; otherwise no chain is known.
  vector<string> IncludeChainForUse(
      clang::SourceLocation use_loc,
      clang::SourceLocation declaration_loc,
      const string& normalized_selected_path) const;

  // Returns true when the preprocessor branch active at provider_loc is also
  // active at use_loc. An unconditional provider dominates every use in the
  // same include instance; a conditional provider dominates only uses in the
  // same branch or a nested branch. Unknown locations fail closed.
  bool ConditionAtLocationDominates(clang::SourceLocation provider_loc,
                                    clang::SourceLocation use_loc) const;

  enum class BranchActivity { kActive, kInactive, kUnknown };
  enum class LexicalScope { kFile, kNonFile, kUnknown };

  struct ActiveCondition {
    enum class Status { kNotApplicable, kKnown, kUnknown };
    Status status = Status::kUnknown;
    string expression;
    clang::SourceLocation directive_location;
    bool active_branch = false;
    // Source-derived identity. Unlike expression text, this binds a condition
    // to exact directive offsets and its complete nesting path.
    string source_region_id;
    vector<string> source_region_path;
  };

  struct ConditionalRegion {
    string source_region_id;
    unsigned file_id;
    unsigned opening_offset;
    unsigned directive_offset;
    unsigned body_begin_offset;
    unsigned body_end_offset;
    bool has_body_end;
    unsigned branch_index;
    string parent_source_region_id;
    string directive_kind;
    string expression;
    BranchActivity activity;
    LexicalScope lexical_scope;
    clang::SourceLocation opening_location;
    clang::SourceLocation directive_location;
  };

  ActiveCondition ActiveConditionAt(clang::SourceLocation loc) const;
  vector<ConditionalRegion> ConditionalRegionsFor(
      const clang::FileEntry* file) const;
  ActiveCondition DirectIncludeConditionAt(
      const clang::FileEntry* includer, const string& quoted_include,
      int line_number) const;

  enum class DirectIncludeCoverage {
    kNoDirectOccurrence,
    kCoversUse,
    kDoesNotCoverUse,
    kUnknown,
  };

  // Returns condition-aware coverage for direct occurrences of quoted_include
  // in includer. Associated-header and other effective includes are reported as
  // kNoDirectOccurrence so callers can preserve their existing semantics until
  // those edges carry native condition provenance too.
  DirectIncludeCoverage DirectIncludeCoverageAt(
      const clang::FileEntry* includer, const string& quoted_include,
      clang::SourceLocation use_loc) const;

  // Proves that an exact selected direct include occurrence makes declaration
  // available at use_loc. The selected path must resolve unambiguously. The
  // declaration may live in the selected provider itself or in one
  // unconditional header directly included by that exact provider instance.
  // Skipped re-entry, a different entered instance, deeper providers, and
  // unknown source facts fail closed.
  DirectIncludeCoverage DirectProviderCoverageAt(
      const clang::FileEntry* includer, const string& selected_spelling,
      const string& selected_path, clang::SourceLocation declaration_loc,
      clang::SourceLocation use_loc) const;

  // As above for a provider reached through an exact associated-header
  // occurrence. The declaration may live in the associated header itself or
  // in the exact selected header directly included by that associated
  // instance. No transitive provider graph is inferred.
  DirectIncludeCoverage AssociatedProviderCoverageAt(
      const clang::FileEntry* includer,
      const clang::FileEntry* associated_header,
      const string& selected_spelling, const string& selected_path,
      clang::SourceLocation declaration_loc,
      clang::SourceLocation use_loc) const;

  // Proves only the root associated-header occurrence and declaration
  // instance, for forward declarations supplied by the associated header.
  DirectIncludeCoverage AssociatedDeclarationCoverageAt(
      const clang::FileEntry* includer,
      const clang::FileEntry* associated_header,
      clang::SourceLocation declaration_loc,
      clang::SourceLocation use_loc) const;

  // Return true if the given file has
  // "// IWYU pragma: no_include <other_filename>".
  bool IncludeIsInhibited(const clang::FileEntry* file,
                          const string& other_filename) const;

  // Return true if the given file has
  // "// IWYU pragma: no_forward_declare <qualified_symbol_name>".
  bool ForwardDeclareIsInhibited(
      const clang::FileEntry* file, const string& qualified_symbol_name) const;

  // Return true if the fwd decl is marked with "IWYU pragma: keep".
  bool ForwardDeclareIsMarkedKeep(const clang::NamedDecl* decl) const;

  // Return true if the fwd decl is marked with "IWYU pragma: export".
  bool ForwardDeclareIsExported(const clang::NamedDecl* decl) const;

 protected:
  // Preprocessor event handlers called by Clang.
  void MacroExpands(const clang::Token& macro_use_token,
                    const clang::MacroDefinition& definition,
                    clang::SourceRange range,
                    const clang::MacroArgs* args) override;
  void MacroDefined(const clang::Token& id,
                    const clang::MacroDirective* directive) override;
  void MacroUndefined(const clang::Token& id,
                      const clang::MacroDefinition& definition,
                      const clang::MacroDirective* directive) override;

  void Ifdef(clang::SourceLocation loc, const clang::Token& id,
             const clang::MacroDefinition& definition) override;
  void Ifndef(clang::SourceLocation loc, const clang::Token& id,
              const clang::MacroDefinition& definition) override;
  void Defined(const clang::Token& id,
               const clang::MacroDefinition& definition,
               clang::SourceRange range) override;

  void If(clang::SourceLocation loc, clang::SourceRange condition_range,
          ConditionValueKind condition_value) override;
  void Elif(clang::SourceLocation loc, clang::SourceRange condition_range,
            ConditionValueKind condition_value,
            clang::SourceLocation if_loc) override;
  void Elifdef(clang::SourceLocation loc, const clang::Token& id,
               const clang::MacroDefinition& definition) override;
  void Elifdef(clang::SourceLocation loc, clang::SourceRange condition_range,
               clang::SourceLocation if_loc) override;
  void Elifndef(clang::SourceLocation loc, const clang::Token& id,
                const clang::MacroDefinition& definition) override;
  void Elifndef(clang::SourceLocation loc, clang::SourceRange condition_range,
                clang::SourceLocation if_loc) override;
  void Else(clang::SourceLocation loc, clang::SourceLocation if_loc) override;
  void Endif(clang::SourceLocation loc, clang::SourceLocation if_loc) override;

  void InclusionDirective(clang::SourceLocation hash_loc,
                          const clang::Token& include_token,
                          llvm::StringRef filename,
                          bool is_angled,
                          clang::CharSourceRange filename_range,
                          clang::OptionalFileEntryRef file,
                          llvm::StringRef search_path,
                          llvm::StringRef relative_path,
                          const clang::Module* imported,
                          clang::SrcMgr::CharacteristicKind file_type) override;

  void FileChanged(clang::SourceLocation loc, FileChangeReason reason,
                   clang::SrcMgr::CharacteristicKind file_type,
                   clang::FileID exiting_from_id) override;
  void FileSkipped(const clang::FileEntryRef& file, const clang::Token &filename,
                   clang::SrcMgr::CharacteristicKind file_type) override;
  // FileChanged is actually a multi-plexer for 4 different callbacks.
  void FileChanged_EnterFile(clang::SourceLocation file_beginning);
  void FileChanged_ExitToFile(clang::SourceLocation include_loc,
                              const clang::FileEntry* exiting_from);
  void FileChanged_RenameFile(clang::SourceLocation new_file);
  void FileChanged_SystemHeaderPragma(clang::SourceLocation loc);

  // CommentHandler callback.
  // Clang doc: The handler shall return true if it has pushed any
  // tokens to be read using e.g. EnterToken or EnterTokenStream.
  bool HandleComment(clang::Preprocessor& pp,
                     clang::SourceRange comment_range) override;

 private:
  struct ConditionalState {
    const clang::IdentifierInfo* macro;
    bool is_ifndef;
    bool in_alternative;
  };

  // Source-derived branch identity for one active #if/#elif/#else group.
  // FileID distinguishes repeated include instances of one physical file.
  struct ActiveConditionalState {
    unsigned file_id;
    unsigned opening_location;
    unsigned branch_index;
    string expression;
    clang::SourceLocation directive_location;
    vector<string> prior_branch_expressions;
    const clang::IdentifierInfo* macro;
    bool is_ifndef;
    BranchActivity parent_activity;
    bool prior_active;
    bool prior_unknown;
    BranchActivity activity;
    LexicalScope lexical_scope;
    string source_region_id;
    size_t region_fact_index;

    bool operator==(const ActiveConditionalState& other) const {
      return file_id == other.file_id &&
             opening_location == other.opening_location &&
             branch_index == other.branch_index;
    }
  };

  struct ConditionEvent {
    unsigned file_id;
    unsigned offset;
    vector<ActiveConditionalState> path;
  };

  struct DirectIncludeCondition {
    const clang::FileEntry* includer;
    const clang::FileEntry* includee;
    string quoted_include;
    clang::SourceLocation location;
    clang::FileID includee_file_id;
  };

  DirectIncludeCoverage DirectOccurrenceCoverageAt(
      const DirectIncludeCondition& include,
      clang::SourceLocation use_loc) const;
  bool DirectOccurrenceIsUnconditional(
      const DirectIncludeCondition& include, bool* unknown) const;
  bool SkippedDottedInlineOccurrenceProvides(
      const DirectIncludeCondition& include,
      clang::SourceLocation declaration_loc,
      clang::SourceLocation use_loc) const;

  struct PendingDottedInlineExport {
    const clang::FileEntry* includer;
    const clang::FileEntry* includee;
    string include_name_as_written;
    vector<ConditionalState> conditions;
  };

  void EnterConditional(clang::SourceLocation loc,
                        const clang::IdentifierInfo* macro,
                        bool is_ifndef, const string& expression,
                        BranchActivity condition_activity,
                        const string& directive_kind);
  void EnterAlternative(clang::SourceLocation loc, const string& expression,
                        bool is_else, BranchActivity condition_activity,
                        const string& directive_kind);
  void ExitConditional(clang::SourceLocation loc);
  BranchActivity ActivityUnderParent(BranchActivity condition_activity,
                                     BranchActivity parent_activity) const;
  unsigned DirectiveBodyBeginOffset(clang::SourceLocation loc) const;
  clang::SourceLocation DirectiveKeywordLocation(
      clang::SourceLocation loc, const string& directive_kind) const;
  void CloseCurrentRegion(unsigned file_id, unsigned end_offset);
  string ConditionSourceText(clang::SourceRange range) const;
  void RecordConditionEvent(clang::SourceLocation loc);
  bool ConditionPathAt(clang::SourceLocation loc,
                       vector<ActiveConditionalState>* path) const;
  void MaybeRecordDottedInlineExport(
      clang::SourceLocation includer_loc, const clang::FileEntry* includee,
      const string& include_name_as_written);
  void FinalizeDottedInlineExports();

  // Returns true if includee is considered part of the main
  // compilation unit.  We always generate warnings for violations in
  // files are part of the main compilation unit.
  bool BelongsToMainCompilationUnit(const clang::FileEntry* includer,
                                    const clang::FileEntry* includee) const;

  // Creates a new iwyu_file_info_map_[file_entry] if it doesn't exist,
  // or a noop otherwise.  quoted_include_name is used to create the
  // new entry if necessary.
  void InsertIntoFileInfoMap(const clang::FileEntry* file,
                             const string& quoted_include_name);

  // Helper function that returns iwyu_file_info_map_[file_entry] if
  // it already exists, or creates a new one and returns it otherwise.
  // If it creates a new one, it generates the quoted_include_name
  // from the file-path for 'file'.
  // TODO(csilvers): see if, in practice, all uses in here are just 'get's.
  IwyuFileInfo* GetFromFileInfoMap(const clang::FileEntry* file);

  // Helper for AddDirectInclude.  Checks if we should protect the
  // #include from iwyu removal.
  void MaybeProtectInclude(clang::SourceLocation includer_loc,
                           const clang::FileEntry* includee,
                           const string& include_name_as_written);

  // Called whenever an #include is seen in the preprocessor output.
  void AddDirectInclude(clang::SourceLocation includer_loc,
                        const clang::FileEntry* includee,
                        const string& include_name_as_written,
                        clang::FileID includee_file_id = clang::FileID());

  // Report a "begin_exports"/"end_exports" pragma pair.
  // begin_line is first line, end_line is just after the last line.
  void AddExportedRange(const clang::FileEntry* file,
                        int begin_line, int end_line);

  // Determine if the comment is a pragma, and if so, process it.
  void HandlePragmaComment(clang::SourceRange comment_range);

  // Process @headername directives in a file.
  void ProcessHeadernameDirectivesInFile(clang::SourceLocation file_beginning);

  // Checks whether it's OK to use the given macro defined in file defined_in.
  void ReportMacroUse(const string& name,
                      clang::SourceLocation usage_location,
                      clang::SourceLocation dfn_location);

  // As above, but get the definition location from macros_definition_loc_.
  void FindAndReportMacroUse(const string& name, clang::SourceLocation loc);

  // Final-processing routines done after all header files have been read.
  void DoFinalMacroChecks();
  // Helper for PopulateIntendsToProvideMap().
  void AddAllIncludesAsFileEntries(const clang::FileEntry* includer,
                                   set<const clang::FileEntry*>* retval) const;
  void PopulateIntendsToProvideMap();
  void PopulateTransitiveIncludeMap();
  void FinalizeProtectedIncludes();

  // Return true if at the current point in the parse of the given file,
  // there is a pending "begin_exports" pragma.
  bool HasOpenBeginExports(const clang::FileEntry* file) const;

  // Return true if at the current point in the parse of the given files,
  // there is a pending "begin_keep" pragma.
  bool HasOpenBeginKeep(const clang::FileEntry* file) const;

  clang::Preprocessor* preprocessor_;
  unsigned expanded_brace_depth_;
  bool expanded_scope_unknown_;
  map<const clang::FileEntry*, vector<ConditionalState>> conditional_stacks_;
  map<unsigned, vector<ActiveConditionalState>> active_condition_stacks_;
  map<unsigned, vector<ConditionEvent>> condition_events_;
  map<const clang::FileEntry*, vector<clang::FileID>> entered_file_ids_;
  vector<ConditionalRegion> conditional_regions_;
  map<const clang::FileEntry*, vector<size_t>> conditional_regions_by_file_;
  vector<DirectIncludeCondition> direct_include_conditions_;
  vector<PendingDottedInlineExport> pending_dotted_inline_exports_;

  // The C++ source file passed in as an argument to the compiler (as
  // opposed to other files seen via #includes).
  const clang::FileEntry* main_file_;

  // All files that we should report iwyu violations in.  It defaults
  // to the "main compilation unit" (e.g. foo.cc, foo.h, foo-inl.h):
  // main_file_ and its associated .h and -inl.h files, if they exist.
  // But users can add to it via the --check_also flag.
  set<const clang::FileEntry*> files_to_report_iwyu_violations_for_;

  // These store macros seen, as we see them, and also macros that are
  // called from other macros.  We use this to do limited iwyu-testing
  // on macro tokens (we'd love to test macro bodies more completely
  // -- like we do template bodies -- but macros don't give us enough
  // context to know how to interpret the tokens we see, in general).
  map<string, clang::SourceLocation> macros_definition_loc_;  // key: macro name

  struct MacroBodyReference {
    vector<const clang::IdentifierInfo*> components;
    clang::SourceLocation location;
    bool globally_qualified;
    bool was_macro;
  };

  struct MacroBodyDependency {
    const clang::MacroInfo* macro_info;
    clang::SourceLocation definition_location;
    string macro_name;
    vector<MacroBodyReference> references;
    bool retired_in_defining_instance;
  };

  // One immutable record per function-like macro definition. A downstream
  // #undef or redefinition must not erase the self-containment requirements of
  // a header already parsed. An #undef in the same FileID can retire only the
  // exact MacroInfo definition named by Clang.
  vector<MacroBodyDependency> macro_body_dependencies_;

  // This should logically be a set, but set<> needs Token::operator<
  // which we don't have.  Luckily, a vector works just as well.
  vector<clang::Token> macros_called_from_macros_;

  // This maps from the include-name as written in the program
  // (including <>'s or ""'s) to the FileEntry we loaded for that
  // #include.
  map<string, const clang::FileEntry*> include_to_fileentry_map_;

  map<const clang::FileEntry*, IwyuFileInfo> iwyu_file_info_map_;

  // How many #include lines we've encountered from the given file.
  map<const clang::FileEntry*, int> num_includes_seen_;

  // Maps from a FileEntry* to all files that this file "intends" to
  // provide the symbols from.  For now, we say a file intentionally
  // provides a symbol if it defines it, or if any file it directly
  // #includes defines it.  However, if the header is a private header
  // -- as determined by the fact it's in the private->public header
  // map -- we relax the second requirement to allow any file directly
  // or indirectly included by the public file.  This isn't perfect,
  // but is as close as we can be to matching the intent of the author
  // of the public/private system.
  map<const clang::FileEntry*,
      set<const clang::FileEntry*>> intends_to_provide_map_;

  // Maps from a FileEntry* to all the files that this file includes,
  // either directly or indirectly.
  map<const clang::FileEntry*,
      set<const clang::FileEntry*>> transitive_include_map_;

  // Maps from a FileEntry* to the quoted names of files that its file
  // is directed *not* to include via the "no_include" pragma.
  map<const clang::FileEntry*, set<string>> no_include_map_;

  // Maps from a FileEntry* to the qualified names of symbols that its
  // file is directed *not* to forward-declare via the
  // "no_forward_declare" pragma.
  map<const clang::FileEntry*, set<string>> no_forward_declare_map_;

  const IwyuFileInfo empty_file_info_;

  // For processing pragmas. It is the current stack of open
  // "begin_exports".  There should be at most one item in this stack
  // per file in the current inclusion chain..
  stack<clang::SourceLocation> begin_exports_location_stack_;

  // For processing pragmas. It is the current stack of open "begin_keep"s.
  // There should be at most one item in this stack per file in the current
  // inclusion chain.
  stack<clang::SourceLocation> begin_keep_location_stack_;

  // For processing forward decls. It is a multimap containing the bounds of
  // every keep range.
  multimap<const clang::FileEntry*, clang::SourceRange> keep_location_ranges_;

  // For processing forward decls. It is a multimap containing the bounds of
  // every export range.
  multimap<const clang::FileEntry*, clang::SourceRange> export_location_ranges_;

  // For processing associated pragma. It is the current open
  // "associated" pragma.
  clang::SourceLocation associated_pragma_location_;

  // Filename spelling location in the last encountered inclusion directive.
  // Should be used only in FileChanged_EnterFile, FileSkipped when
  // corresponding callback is caused by inclusion directive.  Don't use in
  // other places because it is unclear which inclusion directive filename
  // location corresponds to.
  clang::SourceLocation include_filename_loc_;

  // Keeps track of which files have the "always_keep" pragma, so they can be
  // marked as such for all includers.
  std::set<const clang::FileEntry*> always_keep_files_;
};

}  // namespace include_what_you_use

#endif  // INCLUDE_WHAT_YOU_USE_IWYU_PREPROCESSOR_H_
