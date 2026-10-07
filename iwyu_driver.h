//===--- iwyu_driver.h - iwyu driver implementation -----------*- C++ -*---===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#ifndef INCLUDE_WHAT_YOU_USE_IWYU_DRIVER_H_
#define INCLUDE_WHAT_YOU_USE_IWYU_DRIVER_H_

#include <string>
#include <vector>

namespace clang {
class CompilerInstance;
}

namespace include_what_you_use {

// Expands nested @response files using Clang's GNU or clang-cl tokenization
// mode. Missing response files remain literal arguments. Returns false for a
// recursive response-file cycle and sets error_message.
bool ExpandResponseFiles(int argc, const char** argv,
                         std::vector<std::string>* expanded,
                         std::string* error_message);

// Creates a CompilerInstance object based on the commandline arguments, or
// NULL if there's an error of some sort.
clang::CompilerInstance* CreateCompilerInstance(int argc, const char **argv);

}  // namespace include_what_you_use

#endif  // INCLUDE_WHAT_YOU_USE_IWYU_DRIVER_H_
