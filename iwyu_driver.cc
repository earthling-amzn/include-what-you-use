//===--- iwyu_driver.cc - iwyu driver implementation ----------------------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

// Driver, used to set up a clang CompilerInstance based on argc and
// argv.

// Everything below is adapted from clang/examples/clang-interpreter/main.cpp.

#include <cstdint>
#include <cstring>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "iwyu_globals.h"
#include "llvm/ADT/SmallString.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/Support/Allocator.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/ErrorOr.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/Path.h"
#include "llvm/Support/StringSaver.h"
#include "llvm/TargetParser/Host.h"
#include "llvm/TargetParser/Triple.h"
#include "clang/Basic/DiagnosticOptions.h"
#include "clang/Basic/DiagnosticFrontend.h"
#include "clang/Driver/Compilation.h"
#include "clang/Driver/Driver.h"
#include "clang/Driver/Tool.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/CompilerInvocation.h"
#include "clang/Frontend/FrontendAction.h"
#include "clang/Frontend/TextDiagnosticPrinter.h"

namespace llvm {
class LLVMContext;
}  // namespace llvm

using clang::CompilerInstance;
using clang::CompilerInvocation;
using clang::DiagnosticIDs;
using clang::DiagnosticOptions;
using clang::DiagnosticsEngine;
using clang::TextDiagnosticPrinter;
using clang::driver::Command;
using clang::driver::Compilation;
using clang::driver::Driver;
using clang::driver::JobList;
using llvm::ErrorOr;
using llvm::IntrusiveRefCntPtr;
using llvm::SmallString;
using llvm::SmallVector;
using llvm::SmallVectorImpl;
using llvm::MemoryBuffer;
using llvm::StringRef;
using llvm::cast;
using llvm::errs;
using llvm::isa;
using llvm::opt::ArgStringList;
using llvm::raw_svector_ostream;
using llvm::sys::getDefaultTargetTriple;
using std::set;
using std::unique_ptr;

namespace include_what_you_use {

namespace {

std::string GetExecutablePath(const char *Argv0) {
  // This just needs to be some symbol in the binary; C++ doesn't
  // allow taking the address of ::main however.
  void *main_addr = (void*) (intptr_t) GetExecutablePath;
  return llvm::sys::fs::getMainExecutable(Argv0, main_addr);
}

const char *SaveStringInSet(std::set<std::string> &SavedStrings, StringRef S) {
  return SavedStrings.insert(S.str()).first->c_str();
}

bool UsesClResponseSyntax(int argc, const char** argv) {
  bool cl_mode = false;
  const StringRef executable = llvm::sys::path::stem(argv[0]);
  if (executable.equals_insensitive("cl") ||
      executable.equals_insensitive("clang-cl")) {
    cl_mode = true;
  }
  for (int index = 1; index < argc; ++index) {
    const StringRef argument(argv[index]);
    if (!argument.startswith("--driver-mode="))
      continue;
    cl_mode = argument.substr(strlen("--driver-mode=")) == "cl";
  }
  return cl_mode;
}

std::string AbsoluteResponsePath(StringRef path) {
  SmallString<256> absolute(path);
  if (llvm::sys::fs::make_absolute(absolute))
    return path.str();
  llvm::sys::path::remove_dots(absolute, /*remove_dot_dot=*/true);
  return absolute.str().str();
}

bool ExpandResponseArgument(const std::string& argument, bool cl_mode,
                            std::vector<std::string>* expanded,
                            std::set<std::string>* response_stack,
                            std::string* error_message) {
  if (argument.size() < 2 || argument[0] != '@') {
    expanded->push_back(argument);
    return true;
  }

  ErrorOr<unique_ptr<MemoryBuffer>> buffer =
      MemoryBuffer::getFile(argument.substr(1));
  if (!buffer) {
    expanded->push_back(argument);
    return true;
  }

  const std::string response_path = AbsoluteResponsePath(argument.substr(1));
  if (!response_stack->insert(response_path).second) {
    *error_message = "recursive response file in direct invocation.";
    return false;
  }

  llvm::BumpPtrAllocator allocator;
  llvm::StringSaver saver(allocator);
  SmallVector<const char*, 32> tokens;
  const StringRef contents = buffer.get()->getBuffer();
  if (cl_mode) {
    llvm::cl::TokenizeWindowsCommandLine(contents, saver, tokens);
  } else {
    llvm::cl::TokenizeGNUCommandLine(contents, saver, tokens);
  }
  for (const char* token : tokens) {
    if (!ExpandResponseArgument(token, cl_mode, expanded, response_stack,
                                error_message)) {
      return false;
    }
  }
  response_stack->erase(response_path);
  return true;
}

}  // anonymous namespace

bool ExpandResponseFiles(int argc, const char** argv,
                         std::vector<std::string>* expanded,
                         std::string* error_message) {
  expanded->clear();
  error_message->clear();
  const bool cl_mode = UsesClResponseSyntax(argc, argv);
  std::set<std::string> response_stack;
  for (int index = 0; index < argc; ++index) {
    if (!ExpandResponseArgument(argv[index], cl_mode, expanded,
                                &response_stack, error_message)) {
      return false;
    }
  }
  return true;
}

CompilerInstance* CreateCompilerInstance(int argc, const char **argv) {
  std::string path = GetExecutablePath(argv[0]);
  IntrusiveRefCntPtr<DiagnosticOptions> diagnostic_options =
    new DiagnosticOptions;
  auto* diagnostic_client =
    new TextDiagnosticPrinter(errs(), &*diagnostic_options);

  IntrusiveRefCntPtr<DiagnosticIDs> diagnostic_id(new DiagnosticIDs());
  DiagnosticsEngine diagnostics(diagnostic_id, &*diagnostic_options,
                                diagnostic_client);
  Driver driver(path, getDefaultTargetTriple(), diagnostics);
  driver.setTitle("include what you use");

  // Expand response files with the same tokenizer used for direct command
  // identity, so the digest describes the compiler's effective argv exactly.
  std::vector<std::string> expanded_arguments;
  std::string expansion_error;
  if (!ExpandResponseFiles(argc, argv, &expanded_arguments, &expansion_error)) {
    errs() << "FATAL ERROR: " << expansion_error << '\n';
    return nullptr;
  }
  set<std::string> SavedStrings;
  SmallVector<const char*, 256> args;
  for (const std::string& argument : expanded_arguments)
    args.push_back(SaveStringInSet(SavedStrings, argument));

  // Drop -save-temps arguments to avoid multiple compilation jobs.
  llvm::erase_if(args, [](const char *v) {
    StringRef arg(v);
    return arg.startswith("-save-temps") || arg.startswith("--save-temps");
  });

  // FIXME: This is a hack to try to force the driver to do something we can
  // recognize. We need to extend the driver library to support this use model
  // (basically, exactly one input, and the operation mode is hard wired).
  args.push_back("-fsyntax-only");

  unique_ptr<Compilation> compilation(driver.BuildCompilation(args));
  if (!compilation)
    return nullptr;

  ParseToolChain(compilation->getDefaultToolChain());

  // FIXME: This is copied from ASTUnit.cpp; simplify and eliminate.

  // We expect to get back exactly one command job, if we didn't something
  // failed. Extract that job from the compilation.
  const JobList& jobs = compilation->getJobs();
  if (jobs.size() != 1 || !isa<Command>(*jobs.begin())) {
    SmallString<256> msg;
    raw_svector_ostream out(msg);
    jobs.Print(out, "; ", true);
    diagnostics.Report(clang::diag::err_fe_expected_compiler_job) << out.str();
    return nullptr;
  }

  const Command& command = cast<Command>(*jobs.begin());
  if (StringRef(command.getCreator().getName()) != "clang") {
    diagnostics.Report(clang::diag::err_fe_expected_clang_command);
    return nullptr;
  }

  // Initialize a compiler invocation object from the clang (-cc1) arguments.
  const ArgStringList &cc_arguments = command.getArguments();
  std::shared_ptr<CompilerInvocation> invocation(new CompilerInvocation);
  CompilerInvocation::CreateFromArgs(*invocation, cc_arguments, diagnostics);
  invocation->getFrontendOpts().DisableFree = false;

  // Show the invocation, with -v.
  if (invocation->getHeaderSearchOpts().Verbose) {
    errs() << "clang invocation:\n";
    jobs.Print(errs(), "\n", true);
    errs() << "\n";
  }

  // FIXME: This is copied from cc1_main.cpp; simplify and eliminate.

  // Create a compiler instance to handle the actual work.
  // The caller will be responsible for freeing this.
  CompilerInstance* compiler = new CompilerInstance;
  compiler->setInvocation(invocation);

  // Create the compilers actual diagnostics engine.
  compiler->createDiagnostics();
  if (!compiler->hasDiagnostics())
    return nullptr;

  return compiler;
}

}  // namespace include_what_you_use
