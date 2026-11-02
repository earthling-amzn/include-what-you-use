#!/usr/bin/env python3

##===--- iwyu_tool.py -----------------------------------------------------===##
#
#                     The LLVM Compiler Infrastructure
#
# This file is distributed under the University of Illinois Open Source
# License. See LICENSE.TXT for details.
#
##===----------------------------------------------------------------------===##

""" Driver to consume a Clang compilation database and invoke IWYU.

Example usage with CMake:

  # Unix systems
  $ mkdir build && cd build
  $ CC="clang" CXX="clang++" cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON ...
  $ iwyu_tool.py -p .

  # Windows systems
  $ mkdir build && cd build
  $ cmake -DCMAKE_CXX_COMPILER="%VCINSTALLDIR%/bin/cl.exe" \
    -DCMAKE_C_COMPILER="%VCINSTALLDIR%/VC/bin/cl.exe" \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -G Ninja ...
  $ python3 iwyu_tool.py -p .

Structured multi-configuration JSON:

  $ iwyu_tool.py -o json --output-schema 2 \
      --database server=build/server --database zero=build/zero

See iwyu_tool.py -h for more details on command-line arguments.
"""
from __future__ import print_function
import os
import posixpath
import re
import sys
import json
import time
import shlex
import argparse
import hashlib
import tempfile
import subprocess
import stat
from collections import OrderedDict


JSON_FORMAT = 'json'
RESERVED_JSON_OPTIONS = frozenset([
    '--output_format',
    '--output_schema',
    '--invocation_index',
    '--invocation_id',
    '--command_digest',
    '--translation_unit',
])
NAMED_DATABASE_RESERVED_JSON_OPTIONS = frozenset(
    set(RESERVED_JSON_OPTIONS) | {'--configuration'})
CONFIGURATION_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
# Keep this in the same order-independent name set as CommandlineFlags::ParseArgv
# in iwyu_globals.cc. getopt_long accepts a prefix only when it identifies one
# option unambiguously, except that an exact match always wins.
IWYU_LONG_OPTIONS = frozenset([
    '--check_also',
    '--keep',
    '--transitive_includes_only',
    '--verbose',
    '--mapping_file',
    '--no_default_mappings',
    '--prefix_header_includes',
    '--pch_in_code',
    '--max_line_length',
    '--comment_style',
    '--no_comments',
    '--update_comments',
    '--no_fwd_decls',
    '--quoted_includes_first',
    '--cxx17ns',
    '--error',
    '--error_always',
    '--debug',
    '--regex',
    '--experimental',
    '--output_format',
    '--output_schema',
    '--invocation_index',
    '--invocation_id',
    '--command_digest',
    '--translation_unit',
    '--project_policy',
    '--configuration',
])

CORRECT_RE = re.compile(r'^\((.*?) has correct #includes/fwd-decls\)$')
SHOULD_ADD_RE = re.compile(r'^(.*?) should add these lines:$')
ADD_RE = re.compile('^(.*?) +// (.*)$')
SHOULD_REMOVE_RE = re.compile(r'^(.*?) should remove these lines:$')
FULL_LIST_RE = re.compile(r'The full include-list for (.*?):$')
END_RE = re.compile(r'^---$')
LINES_RE = re.compile(r'^- (.*?)  // lines ([0-9]+)-[0-9]+$')


GENERAL, ADD, REMOVE, LIST = range(4)


def clang_formatter(output):
    """ Process iwyu's output into something clang-like. """
    formatted = []

    state = (GENERAL, None)
    for line in output.splitlines():
        match = CORRECT_RE.match(line)
        if match:
            formatted.append('%s:1:1: note: #includes/fwd-decls are correct' %
                             match.groups(1))
            continue
        match = SHOULD_ADD_RE.match(line)
        if match:
            state = (ADD, match.group(1))
            continue
        match = SHOULD_REMOVE_RE.match(line)
        if match:
            state = (REMOVE, match.group(1))
            continue
        match = FULL_LIST_RE.match(line)
        if match:
            state = (LIST, match.group(1))
        elif END_RE.match(line):
            state = (GENERAL, None)
        elif not line.strip():
            continue
        elif state[0] == GENERAL:
            formatted.append(line)
        elif state[0] == ADD:
            match = ADD_RE.match(line)
            if match:
                formatted.append("%s:1:1: error: add '%s' (%s)" %
                                 (state[1], match.group(1), match.group(2)))
            else:
                formatted.append("%s:1:1: error: add '%s'" % (state[1], line))
        elif state[0] == REMOVE:
            match = LINES_RE.match(line)
            line_no = match.group(2) if match else '1'
            formatted.append("%s:%s:1: error: superfluous '%s'" %
                             (state[1], line_no, match.group(1)))

    return os.linesep.join(formatted)


DEFAULT_FORMAT = 'iwyu'
FORMATTERS = {
    'iwyu': lambda output: output,
    'clang': clang_formatter
}


if sys.platform.startswith('win'):
    # Case-insensitive match on Windows
    def normcase(s):
        return s.lower()
else:
    def normcase(s):
        return s


def is_subpath_of(path, parent):
    """ Return True if path is equal to or fully contained within parent.

    Assumes both paths are canonicalized with os.path.realpath.
    """
    parent = normcase(parent)
    path = normcase(path)

    if path == parent:
        return True

    if not path.startswith(parent):
        return False

    # Now we know parent is a prefix of path, but they only share lineage if the
    # difference between them starts with a path separator, e.g. /a/b/c/file
    # is not a parent of /a/b/c/file.cpp, but /a/b/c and /a/b/c/ are.
    parent = parent.rstrip(os.path.sep)
    suffix = path[len(parent):]
    return suffix.startswith(os.path.sep)


def is_msvc_driver(compile_command):
    """ Return True if compile_command matches an MSVC CL-style driver. """
    compile_command = normcase(compile_command)

    if compile_command.endswith('cl.exe'):
        # Native MSVC compiler or clang-cl.exe
        return True

    if compile_command.endswith('clang-cl'):
        # Cross clang-cl on non-Windows
        return True

    return False


def win_split(cmdline):
    """ Minimal implementation of shlex.split for Windows following
    https://msdn.microsoft.com/en-us/library/windows/desktop/17w5ykft.aspx.
    """
    def split_iter(cmdline):
        in_quotes = False
        backslashes = 0
        arg = ''
        arg_started = False
        for c in cmdline:
            if c == '\\':
                # MSDN: Backslashes are interpreted literally, unless they
                # immediately precede a double quotation mark.
                # Buffer them until we know what comes next.
                backslashes += 1
                arg_started = True
            elif c == '"':
                # Quotes can either be an escaped quote or the start of a quoted
                # string. Paraphrasing MSDN:
                # Before quotes, place one backslash in the arg for every pair
                # of leading backslashes. If the number of backslashes is odd,
                # retain the double quotation mark, otherwise interpret it as a
                # string delimiter and switch state.
                arg_started = True
                arg += '\\' * (backslashes // 2)
                if backslashes % 2 == 1:
                    arg += c
                else:
                    in_quotes = not in_quotes
                backslashes = 0
            elif c in (' ', '\t') and not in_quotes:
                # MSDN: Arguments are delimited by white space, which is either
                # a space or a tab [but only outside of a string].
                # Flush any buffered backslashes and yield every argument,
                # including one introduced by an explicit empty quote pair.
                arg += '\\' * backslashes
                if arg_started:
                    yield arg
                    arg = ''
                    arg_started = False
                backslashes = 0
            else:
                # Flush buffered backslashes and append.
                arg_started = True
                arg += '\\' * backslashes
                arg += c
                backslashes = 0

        arg += '\\' * backslashes
        if arg_started:
            yield arg

    return list(split_iter(cmdline))


def split_command(cmdstr):
    """ Split a command string into a list, respecting shell quoting. """
    if sys.platform.startswith('win'):
        # shlex.split does not work for Windows command-lines, so special-case
        # to our own implementation.
        cmd = win_split(cmdstr)
    else:
        cmd = shlex.split(cmdstr)

    return cmd


def _json_dumps_canonical(value):
    """Return compact JSON with the escaping required by output schema v1."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def canonical_command_bytes(directory, source_file, arguments):
    """Build the exact UTF-8 command representation used for record identity."""
    command = OrderedDict([
        ('directory', directory),
        ('file', source_file),
        ('arguments', arguments),
    ])
    return _json_dumps_canonical(command).encode('utf-8')


def command_identity(directory, source_file, arguments, database_index):
    """Return (digest, invocation id) for a canonical compilation command."""
    digest_hex = hashlib.sha256(canonical_command_bytes(
        directory, source_file, arguments)).hexdigest()
    digest = 'sha256:' + digest_hex
    invocation_id = 'inv-%06d-%s' % (database_index, digest_hex[:12])
    return digest, invocation_id


def _response_file_arguments(contents, windows):
    if windows:
        return win_split(contents)
    # llvm::cl::TokenizeGNUCommandLine does not emit arguments for explicit
    # empty quote pairs. Python's shlex does, so discard those empty tokens.
    return [argument for argument in shlex.split(contents, posix=True)
            if argument]


def expand_response_files(arguments, directory, windows=False, _stack=None):
    """Recursively expand Clang-style @response files.

    Response-file paths are relative to the compilation command directory.
    Clang removes exactly one leading at-sign, so ``@@args.rsp`` expands a
    readable response file named ``@args.rsp``. If that file does not exist,
    Clang retains the original ``@@args.rsp`` argument, as this function does.
    """
    stack = [] if _stack is None else _stack
    expanded = []
    for argument in arguments:
        if not argument.startswith('@') or len(argument) == 1:
            expanded.append(argument)
            continue

        response_path = argument[1:]
        if not os.path.isabs(response_path):
            response_path = os.path.join(directory, response_path)
        response_path = os.path.realpath(response_path)
        if response_path in stack:
            raise ValueError('recursive response file: %s' % response_path)

        try:
            with open(response_path, 'r', encoding='utf-8-sig') as response:
                contents = response.read()
        except (IOError, UnicodeError):
            expanded.append(argument)
            continue

        nested = _response_file_arguments(contents, windows)
        expanded.extend(expand_response_files(
            nested, directory, windows=windows, _stack=stack + [response_path]))
    return expanded


def _resolve_iwyu_long_option(argument):
    """Return the long option selected by IWYU's getopt_long, if any."""
    option = argument.split('=', 1)[0]
    if not option.startswith('--') or len(option) == 2:
        return None
    if option in IWYU_LONG_OPTIONS:
        return option
    matches = [candidate for candidate in IWYU_LONG_OPTIONS
               if candidate.startswith(option)]
    if len(matches) == 1:
        return matches[0]
    return None


def reject_reserved_json_args(
        arguments, reserved_options=RESERVED_JSON_OPTIONS):
    """Reject effective -Xiwyu pairs owned by the driver JSON protocol."""
    for index, argument in enumerate(arguments[:-1]):
        if argument != '-Xiwyu':
            continue
        iwyu_argument = arguments[index + 1]
        if _resolve_iwyu_long_option(iwyu_argument) in reserved_options:
            raise ValueError("reserved IWYU driver argument: '%s'" %
                             iwyu_argument)


HEADER_SUFFIX_RE = re.compile(
    r'(?i)(?:\.h|\.hh|\.hpp|\.hxx|\.inc|\.inl)$')
# Options whose operand names an output. Both tokens are removed.
SYNTHETIC_OUTPUT_OPTIONS = frozenset([
    '-o', '--output', '-MF', '-MT', '-MQ', '-MJ',
    '-dependency-file', '--dependency-file',
    '-serialize-diagnostics', '--serialize-diagnostics',
    '-fdiagnostics-serialization-file', '-foptimization-record-file',
    '-fprofile-dir', '-fprofile-note', '-fprofile-instrument-path',
    '-dependency-dot', '-arcmt-migrate-report-output',
    '-gen-cdb-fragment-path', '-dsym-dir', '-coverage-data-file',
    '-coverage-notes-file', '-split-dwarf-output', '-object-file-name',
    '-dumpdir', '-dumpbase', '/Fo', '/Fd', '/Fe', '/Fa', '/Fi',
])
# Output/action switches that are safe to discard for syntax-only analysis.
SYNTHETIC_OUTPUT_FLAGS = frozenset([
    '-c', '-S', '-E', '-M', '-MM', '-MD', '-MMD', '-MP', '-MG',
    '-save-temps', '--save-temps', '/c', '/showIncludes',
    '--coverage', '-coverage', '-fprofile-arcs', '-ftest-coverage',
    '-fcoverage-mapping', '-fprofile-instr-generate',
    '-fprofile-generate', '-fcs-profile-generate', '-fmemory-profile',
    '-forder-file-instrumentation', '-ftime-trace',
    '-ftime-trace-verbose', '-fsave-optimization-record', '-save-stats',
    '-gen-reproducer', '-fcrash-diagnostics', '-fsyntax-only',
    '-fno-crash-diagnostics', '-via-file-asm',
])
# Joined output destinations and output-enabling switches.
SYNTHETIC_OUTPUT_PREFIXES = (
    '-MF', '-MT', '-MQ', '-MJ', '--output=',
    '-dependency-file=', '--dependency-file=',
    '-serialize-diagnostics=', '--serialize-diagnostics=',
    '-fdiagnostics-serialization-file=', '-foptimization-record-file=',
    '-fprofile-dir=', '-fprofile-note=', '-fprofile-instrument-path=',
    '-dumpdir=', '-dumpbase=', '-save-temps=', '--save-temps=',
    '-fprofile-instr-generate=', '-fprofile-generate=',
    '-fcs-profile-generate=', '-fmemory-profile=', '-ftime-trace=',
    '-ftime-trace-granularity=', '-foptimization-record-passes=',
    '-foptimization-record-format=', '-fsave-optimization-record=',
    '-gen-reproducer=', '-fcrash-diagnostics=',
    '-fcrash-diagnostics-dir=', '-save-stats=', '-stats-file=',
    '-coverage-data-file=', '-coverage-notes-file=',
    '-split-dwarf-output=', '-object-file-name=',
    '-dependency-dot=', '-arcmt-migrate-report-output=',
    '-gen-cdb-fragment-path=', '-dsym-dir=',
    '-fsanitize-coverage=', '-fno-sanitize-coverage=',
)
# GCC's random seed stabilizes generated symbol names and other code-generation
# output. Once the source is replaced by a header and the action is syntax-only,
# it cannot affect preprocessing or AST semantics and must not split otherwise
# identical synthetic parse profiles. Accept both GCC spellings.
SYNTHETIC_NONSEMANTIC_OPTIONS = frozenset(['-frandom-seed'])
SYNTHETIC_NONSEMANTIC_PREFIXES = ('-frandom-seed=',)
# These options can make Clang write module/cache artifacts. Silently removing
# them could change lookup semantics, so header synthesis fails closed instead.
SYNTHETIC_REJECT_OPTIONS = frozenset([
    '-fmodules', '-fcxx-modules', '-fimplicit-modules',
    '-fimplicit-module-maps', '-fmodule-output', '-fmodule-header',
    '-fmodules-cache-path',
    '-fmodules-user-build-path', '-module-dependency-dir',
    '-emit-module', '-emit-module-interface', '-emit-header-unit', '-emit-pch',
    '-emit-interface-stubs', '-emit-llvm', '-emit-ast', '-emit-merged-ifs',
    '--emit-static-lib', '--emit-symbol-graph', '-extract-api', '--analyze',
    '-rewrite-objc', '-rewrite-legacy-objc', '--gpu-bundle-output',
])
SYNTHETIC_REJECT_PREFIXES = (
    '-fmodule-output=', '-fmodule-header=', '-fmodules-cache-path=',
    '-fmodules-user-build-path=',
    '-fcas-path=', '-fcas-plugin-path=', '-fthinlto-index=',
    '-module-dependency-dir=', '--emit-symbol-graph=',
)
# A binary precompiled-header state belongs to the original compilation. It is
# not a reusable semantic input for a different main file. Reject these before
# locating or replacing the source input; preserving or silently removing one
# would both synthesize a profile with unproved semantics.
SYNTHETIC_BINARY_PCH_OPTIONS = frozenset([
    '-include-pch', '-include-pth', '-token-cache', '-chain-include',
    '-pch-through-header', '-pch-through-hdrstop-create',
    '-pch-through-hdrstop-use', '-preamble-bytes', '-print-preamble',
    '-verify-pch', '-emit-pch', '-emit-pth', '-precompile', '--precompile',
    '-relocatable-pch', '-building-pch-with-obj',
])
SYNTHETIC_BINARY_PCH_PREFIXES = (
    '-include-pch', '-include-pth', '-token-cache', '-chain-include',
    '-pch-through-header', '-pch-through-hdrstop-', '-preamble-bytes',
    '-print-preamble=', '-verify-pch=', '-emit-pch=', '-emit-pth=', '-fpch-',
    '-fno-pch-', '-relocatable-pch=', '-building-pch-with-obj=',
)
# Options with a separate operand. Semantic module inputs intentionally remain
# here and are copied unchanged: -fmodule-file, -fmodule-map-file and
# -fprebuilt-module-path (including their joined spellings).
SYNTHETIC_OPTION_OPERANDS = frozenset([
    '-o', '--output', '-MF', '-MT', '-MQ', '-MJ', '-x', '-frandom-seed',
    '-include', '-imacros', '-include-pch', '-isysroot', '--sysroot',
    '-I', '-isystem', '-iquote', '-idirafter', '-iprefix', '-iwithprefix',
    '-iwithprefixbefore', '-F', '-iframework', '-resource-dir',
    '-gcc-toolchain', '-target', '--target', '-B', '-Xclang', '-Xpreprocessor',
    '-Xassembler', '-Xlinker', '-dependency-file', '--dependency-file',
    '-serialize-diagnostics', '--serialize-diagnostics',
    '-fdiagnostics-serialization-file', '-foptimization-record-file',
    '-fprofile-dir', '-fprofile-note', '-fprofile-instrument-path',
    '-dependency-dot', '-arcmt-migrate-report-output',
    '-gen-cdb-fragment-path', '-dsym-dir', '-coverage-data-file',
    '-coverage-notes-file', '-split-dwarf-output', '-object-file-name',
    '-dumpdir', '-dumpbase', '-fmodule-file', '-fmodule-map-file',
    '-fprebuilt-module-path', '/Fo', '/Fd', '/Fe', '/Fa', '/Fi',
])
SYNTHETIC_UNSAFE_XCLANG = frozenset([
    '-emit-module', '-emit-module-interface', '-emit-header-unit', '-emit-pch',
    '-emit-interface-stubs', '-emit-llvm', '-dependency-file',
    '-serialize-diagnostic-file', '-fmodule-output', '-fmodules-cache-path',
])


def _strict_absolute_path(path, description, kind=None):
    """Return a canonical path while rejecting lexical and symlink aliases."""
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        raise ValueError('%s must be an absolute path' % description)
    if os.path.abspath(os.path.normpath(path)) != path:
        raise ValueError('%s must be lexically normalized' % description)
    probe = path
    while probe and probe != os.path.dirname(probe):
        if os.path.islink(probe):
            raise ValueError('%s must not contain a symlink: %s' %
                             (description, probe))
        probe = os.path.dirname(probe)
    if not os.path.exists(path):
        raise ValueError('%s does not exist: %s' % (description, path))
    if kind == 'directory' and not os.path.isdir(path):
        raise ValueError('%s is not a directory: %s' % (description, path))
    if kind == 'file' and not os.path.isfile(path):
        raise ValueError('%s is not a regular file: %s' % (description, path))
    real = os.path.realpath(path)
    if normcase(real) != normcase(path):
        raise ValueError('%s aliases a different physical path: %s' %
                         (description, path))
    return real


def _validate_rooted_header(root, relative_path):
    if (not isinstance(relative_path, str) or not relative_path or
            '\x00' in relative_path or '\\' in relative_path or
            relative_path.startswith('/') or
            any(component in ('', '.', '..')
                for component in relative_path.split('/'))):
        raise ValueError('header path is not a normalized rooted path: %r' %
                         relative_path)
    if not HEADER_SUFFIX_RE.search(relative_path):
        raise ValueError('selected path is not a recognized header: %s' %
                         relative_path)
    lexical = os.path.join(root, *relative_path.split('/'))
    header = _strict_absolute_path(lexical, 'selected header', kind='file')
    if not is_subpath_of(header, root) or header == root:
        raise ValueError('selected header escapes analysis root: %s' %
                         relative_path)
    return header


def _metadata_identity(metadata):
    """Return the replacement-sensitive identity required for a source file."""
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode,
            stat.S_IFMT(metadata.st_mode))


def _source_snapshot(path):
    """Read one stable regular file without following a final symlink."""
    before_lstat = os.lstat(path)
    before_stat = os.stat(path)
    if (stat.S_ISLNK(before_lstat.st_mode) or
            not stat.S_ISREG(before_lstat.st_mode) or
            not stat.S_ISREG(before_stat.st_mode)):
        raise ValueError('snapshot source is not a regular non-symlink file: %s' %
                         path)
    flags = os.O_RDONLY
    if hasattr(os, 'O_CLOEXEC'):
        flags |= os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        contents = b''.join(chunks)
    finally:
        os.close(descriptor)
    after_lstat = os.lstat(path)
    after_stat = os.stat(path)
    lstat_identity = _metadata_identity(before_lstat)
    stat_identity = _metadata_identity(before_stat)
    if (lstat_identity != _metadata_identity(after_lstat) or
            stat_identity != _metadata_identity(after_stat) or
            stat_identity != _metadata_identity(opened) or
            stat.S_ISLNK(after_lstat.st_mode)):
        raise ValueError('snapshot source changed while it was read: %s' % path)
    metadata = (lstat_identity, stat_identity)
    return (contents, 'sha256:' + hashlib.sha256(contents).hexdigest(),
            metadata)


def _canonical_manifest_bytes(root, headers):
    value = OrderedDict([
        ('schema_version', 1),
        ('root', root.replace(os.path.sep, '/')),
        ('headers', [OrderedDict([
            ('path', relative), ('sha256', digest[len('sha256:'):])])
            for relative, _, digest in headers]),
    ])
    return (_json_dumps_canonical(value) + '\n').encode('utf-8')


def select_headers(root, globs=None, manifest=None):
    """Strictly select and snapshot rooted physical headers."""
    root = _strict_absolute_path(root, 'header analysis root', kind='directory')
    globs = list(globs or [])
    if bool(globs) == bool(manifest):
        raise ValueError('specify header globs or one header manifest, not both')

    selected = []
    if manifest:
        manifest = _strict_absolute_path(
            manifest, 'header manifest', kind='file')
        try:
            with open(manifest, 'rb') as stream:
                manifest_bytes = stream.read()
            text = manifest_bytes.decode('utf-8')
            value = json.loads(
                text, object_pairs_hook=_reject_duplicate_object_keys,
                parse_constant=_reject_nonfinite)
        except (IOError, UnicodeError, ValueError) as why:
            raise ValueError('invalid header manifest: %s' % why)
        if (not isinstance(value, dict) or
                list(value.keys()) != ['schema_version', 'root', 'headers'] or
                value.get('schema_version') != 1 or
                value.get('root') != root.replace(os.path.sep, '/') or
                not isinstance(value.get('headers'), list)):
            raise ValueError('header manifest has an invalid canonical shape')
        previous = None
        for item in value['headers']:
            if (not isinstance(item, dict) or
                    list(item.keys()) != ['path', 'sha256'] or
                    not isinstance(item.get('sha256'), str) or
                    re.fullmatch(r'[0-9a-f]{64}', item['sha256']) is None):
                raise ValueError('header manifest entry has an invalid shape')
            relative = item.get('path')
            if previous is not None and relative <= previous:
                raise ValueError('header manifest paths are duplicate or unsorted')
            previous = relative
            path = _validate_rooted_header(root, relative)
            contents, digest, metadata = _source_snapshot(path)
            if item['sha256'] != digest[len('sha256:'):]:
                raise ValueError('header manifest digest mismatch: %s' % relative)
            selected.append((relative, path, digest, contents, metadata))
        canonical = _canonical_manifest_bytes(
            root, [(r, p, d) for r, p, d, _, _ in selected])
        if manifest_bytes != canonical:
            raise ValueError('header manifest is not canonical JSON')
    else:
        import glob as glob_module
        by_physical = {}
        for pattern in globs:
            if (not isinstance(pattern, str) or not pattern or
                    '\x00' in pattern or '\\' in pattern or
                    pattern.startswith('/') or
                    any(component in ('', '.', '..')
                        for component in pattern.split('/'))):
                raise ValueError(
                    'header glob must be a normalized path relative to root: %r' %
                    pattern)
            matches = sorted(glob_module.glob(
                os.path.join(root, *pattern.split('/')), recursive=True))
            file_matches = []
            for match in matches:
                if os.path.isdir(match):
                    continue
                relative = os.path.relpath(match, root).replace(os.path.sep, '/')
                path = _validate_rooted_header(root, relative)
                file_matches.append((relative, path))
            if not file_matches:
                raise ValueError('header glob matched no headers: %s' % pattern)
            for relative, path in file_matches:
                key = normcase(path)
                prior = by_physical.get(key)
                if prior is not None and prior != relative:
                    raise ValueError(
                        'header paths alias one physical file: %s and %s' %
                        (prior, relative))
                by_physical[key] = relative
        for path_key, relative in sorted(
                by_physical.items(), key=lambda item: item[1]):
            path = os.path.join(root, *relative.split('/'))
            contents, digest, metadata = _source_snapshot(path)
            selected.append((relative, path, digest, contents, metadata))
    if not selected:
        raise ValueError('header selection is empty')
    physical_files = {}
    for relative, path, _, _, metadata in selected:
        physical_id = metadata[1][:2]
        prior = physical_files.get(physical_id)
        if prior is not None and prior != relative:
            raise ValueError(
                'header paths alias one physical file: %s and %s' %
                (prior, relative))
        physical_files[physical_id] = relative
    return root, selected


def _verify_source_contents(source_contents):
    for identity, expected in sorted(source_contents.items()):
        try:
            with open(identity, 'rb') as source:
                current = source.read()
        except OSError as why:
            raise ValueError('cannot rehash analyzed source %s: %s' %
                             (identity, why))
        if current != expected:
            raise ValueError('analyzed source changed during analysis: %s' %
                             identity)


def _verify_header_snapshots(headers):
    for relative, path, expected_digest, expected_contents, expected_metadata in headers:
        try:
            contents, digest, metadata = _source_snapshot(path)
        except (OSError, ValueError) as why:
            raise ValueError('selected header changed during analysis: %s (%s)' %
                             (relative, why))
        if (metadata != expected_metadata or digest != expected_digest or
                contents != expected_contents):
            raise ValueError('selected header changed during analysis: %s' %
                             relative)


def _canonical_compile_arguments(entry):
    if 'arguments' in entry:
        command = list(entry['arguments'])
    elif 'command' in entry:
        command = split_command(entry['command'])
    else:
        raise ValueError('Invalid compilation database entry: %s' % entry)
    if not command:
        raise ValueError('Empty compilation command: %s' % entry)
    command = _unwrap_compiler_wrapper(command)
    if not command:
        raise ValueError('Compilation command contains only a wrapper')
    windows_args = is_msvc_driver(command[0])
    directory = os.path.realpath(entry['directory'])
    canonical = expand_response_files(command, directory, windows=windows_args)
    canonical = _unwrap_compiler_wrapper(canonical)
    if not canonical:
        raise ValueError('expanded compilation command contains only a wrapper')
    if not windows_args and is_msvc_driver(canonical[0]):
        windows_args = True
        canonical = expand_response_files(command, directory, windows=True)
        canonical = _unwrap_compiler_wrapper(canonical)
        if not canonical:
            raise ValueError('expanded compilation command contains only a wrapper')
    return canonical, windows_args


def _argument_physical_path(argument, directory):
    if not argument or argument.startswith('-'):
        return None
    path = argument if os.path.isabs(argument) else os.path.join(directory,
                                                                  argument)
    return os.path.realpath(path)


def _source_argument_index(arguments, directory, source):
    operands = set()
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument in SYNTHETIC_OPTION_OPERANDS and index + 1 < len(arguments):
            operands.add(index + 1)
            index += 2
            continue
        index += 1
    positional = [
        (index, argument) for index, argument in enumerate(arguments[1:], 1)
        if index not in operands and not argument.startswith('-')]
    source_like = [
        (index, argument) for index, argument in positional
        if re.search(r'(?i)\.(?:c|cc|cpp|cxx|m|mm|h|hh|hpp|hxx)$', argument)]
    candidates = [
        index for index, argument in positional
        if normcase(_argument_physical_path(argument, directory) or '') ==
        normcase(source)]
    if len(candidates) != 1 or any(
            index != candidates[0] for index, _ in source_like):
        raise ValueError(
            'compile command must contain exactly one replaceable source input')
    return candidates[0]


def _source_kind_language(source):
    if source.endswith('.C'):
        return 'c++'
    lowered = source.lower()
    if lowered.endswith(('.c', '.i')):
        return 'c'
    if lowered.endswith(('.m', '.mi')):
        return 'objective-c'
    if lowered.endswith(('.mm', '.mii')):
        return 'objective-c++'
    return 'c++'


def _normalized_compile_language(language, source):
    normalized = language.lower()
    if normalized == 'none':
        return _source_kind_language(source)
    aliases = {
        'c': 'c', 'c-header': 'c', 'cpp-output': 'c',
        'c++': 'c++', 'c++-header': 'c++', 'c++-cpp-output': 'c++',
        'objective-c': 'objective-c',
        'objective-c-header': 'objective-c',
        'objective-c-cpp-output': 'objective-c',
        'objective-c++': 'objective-c++',
        'objective-c++-header': 'objective-c++',
        'objective-c++-cpp-output': 'objective-c++',
    }
    if normalized not in aliases:
        raise ValueError('unsupported source language for header synthesis: %s' %
                         language)
    return aliases[normalized]


def _compile_language(arguments, source, windows_args):
    if windows_args:
        language = None
        for argument in arguments:
            lowered = normcase(argument)
            if lowered == '/tc':
                language = 'c'
            elif lowered == '/tp':
                language = 'c++'
        return language or _source_kind_language(source)

    language = None
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument == '-x':
            if index + 1 >= len(arguments):
                raise ValueError('compile command has an incomplete -x option')
            language = arguments[index + 1]
            index += 2
            continue
        if argument.startswith('-x') and len(argument) > 2:
            language = argument[2:]
        index += 1
    return (_normalized_compile_language(language, source) if language
            else _source_kind_language(source))


def _is_joined_output_option(argument):
    if (argument.startswith('-o') and len(argument) > 2 and
            not argument.startswith(('-objc', '-object', '-openmp'))):
        return True
    if re.match(r'(?i)^/(?:Fo|Fd|Fe|Fa|Fi).+', argument):
        return True
    return any(argument.startswith(prefix) and
               (prefix.endswith('=') or argument != prefix)
               for prefix in SYNTHETIC_OUTPUT_PREFIXES)


def _is_rejected_synthetic_option(argument):
    return (argument in SYNTHETIC_REJECT_OPTIONS or
            any(argument.startswith(prefix)
                for prefix in SYNTHETIC_REJECT_PREFIXES))


def _binary_pch_option(argument, windows_args):
    """Return the binary PCH/PTH control represented by argument, if any."""
    candidate = (argument.split('=', 1)[1]
                 if argument.startswith('-Xclang=') else argument)
    if (candidate in SYNTHETIC_BINARY_PCH_OPTIONS or
            any(candidate.startswith(prefix)
                for prefix in SYNTHETIC_BINARY_PCH_PREFIXES)):
        return candidate
    if windows_args and candidate.startswith(('/Yu', '-Yu', '/Yc', '-Yc',
                                              '/Fp', '-Fp')):
        return candidate
    return None


def _reject_binary_pch_arguments(arguments, windows_args):
    for argument in arguments[1:]:
        option = _binary_pch_option(argument, windows_args)
        if option is not None:
            raise ValueError(
                'binary precompiled-header option is unsafe for synthetic '
                'header analysis: %s' % option)


def synthetic_compile_profile(entry):
    """Return a deterministic, side-effect-free semantic compile profile."""
    arguments, windows_args = _canonical_compile_arguments(entry)
    _reject_binary_pch_arguments(arguments, windows_args)
    directory = os.path.realpath(entry['directory'])
    source = os.path.realpath(entry['file'])
    source_index = _source_argument_index(arguments, directory, source)
    language = _compile_language(arguments, source, windows_args)
    result = [arguments[0]]
    insert_at = None
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if index == source_index:
            insert_at = len(result)
            index += 1
            continue
        if argument == '-x':
            if index + 1 >= len(arguments):
                raise ValueError('compile command has an incomplete -x option')
            index += 2
            continue
        if argument.startswith('-x') and len(argument) > 2:
            index += 1
            continue
        if argument in SYNTHETIC_NONSEMANTIC_OPTIONS:
            if index + 1 >= len(arguments):
                raise ValueError(
                    'compile command has an incomplete nonsemantic option')
            index += 2
            continue
        if any(argument.startswith(prefix)
               for prefix in SYNTHETIC_NONSEMANTIC_PREFIXES):
            index += 1
            continue
        if _is_rejected_synthetic_option(argument):
            raise ValueError(
                'compile option may write an unsafe synthetic artifact: %s' %
                argument)
        if argument == '-Xclang' and index + 1 < len(arguments):
            xclang_option = arguments[index + 1].split('=', 1)[0]
            if xclang_option in SYNTHETIC_UNSAFE_XCLANG:
                raise ValueError(
                    'unsafe synthetic -Xclang option: %s' %
                    arguments[index + 1])
        if argument in SYNTHETIC_OUTPUT_OPTIONS:
            if index + 1 >= len(arguments):
                raise ValueError('compile command has an incomplete output option')
            index += 2
            continue
        if (argument in SYNTHETIC_OUTPUT_FLAGS or
                _is_joined_output_option(argument)):
            index += 1
            continue
        if windows_args and normcase(argument) in ('/tc', '/tp'):
            index += 1
            continue
        result.append(argument)
        index += 1
    if insert_at is None:
        raise ValueError('compile source input was not replaced')
    replacement = ['/TC' if language == 'c' else '/TP', '{header}'] \
        if windows_args else ['-x', language, '{header}']
    result[insert_at:insert_at] = replacement
    # Force a non-emitting frontend action and disable automatic crash bundles.
    # Both options are semantic no-ops for IWYU's source analysis.
    if not windows_args:
        result.extend(['-fsyntax-only', '-fno-crash-diagnostics'])
    profile_identity = OrderedDict([
        ('directory', directory.replace(os.path.sep, '/')),
        ('arguments', result),
    ])
    return {
        'directory': directory,
        'arguments': result,
        'language': language,
        'key': hashlib.sha256(
            _json_dumps_canonical(profile_identity).encode('utf-8')).hexdigest(),
    }


def instantiate_synthetic_entry(profile, header):
    arguments = [header if argument == '{header}' else argument
                 for argument in profile['arguments']]
    return {'directory': profile['directory'], 'file': header,
            'arguments': arguments}


def _exact_check_also(path):
    # POSIX fnmatch treats a backslash as an escape. Windows selection rejects
    # wildcard-bearing paths because PathMatchSpecA has no exact escaping form.
    if sys.platform.startswith('win') and any(char in path for char in '*?['):
        raise ValueError('selected header path cannot be expressed exactly')
    escaped = ''.join(('\\' + char) if char in '*?[' else char for char in path)
    return ['-Xiwyu', '--check_also=' + escaped.replace(os.path.sep, '/')]



def _entry_with_driver_arguments(entry, driver_arguments):
    arguments, _ = _canonical_compile_arguments(entry)
    return {
        'directory': os.path.realpath(entry['directory']),
        'file': entry['file'],
        'arguments': ([arguments[0]] + list(driver_arguments) + arguments[1:]),
    }
def validate_configuration_name(name):
    """Return a safe non-empty configuration name or raise ValueError."""
    if not isinstance(name, str) or CONFIGURATION_NAME_RE.fullmatch(name) is None:
        raise ValueError(
            "configuration name must match [A-Za-z0-9][A-Za-z0-9_.-]*")
    return name


def parse_database_spec(value):
    """Parse one ordered NAME=PATH compilation-database specification."""
    if '=' not in value:
        raise argparse.ArgumentTypeError(
            "named database must have the form NAME=PATH")
    name, path = value.split('=', 1)
    try:
        validate_configuration_name(name)
    except ValueError as why:
        raise argparse.ArgumentTypeError(str(why))
    if not path:
        raise argparse.ArgumentTypeError(
            "named database path must be non-empty")
    return name, path


def _unwrap_compiler_wrapper(arguments):
    """Return a copy of arguments without one known compiler wrapper."""
    arguments = list(arguments)
    if not arguments:
        return arguments
    wrapper = os.path.basename(normcase(arguments[0]))
    if wrapper.endswith('.exe'):
        wrapper = wrapper[:-4]
    if wrapper in KNOWN_COMPILER_WRAPPERS:
        return arguments[1:]
    return arguments


def _validate_jobs(jobs):
    """Raise ValueError unless jobs is a positive, non-bool integer."""
    if type(jobs) is not int or jobs <= 0:
        raise ValueError('jobs must be a positive integer')


def _positive_jobs(value):
    """Parse a positive job count for argparse."""
    jobs = int(value)
    try:
        _validate_jobs(jobs)
    except ValueError as why:
        raise argparse.ArgumentTypeError(str(why))
    return jobs


def find_include_what_you_use():
    """ Find IWYU executable and return its full pathname. """
    if 'IWYU_BINARY' in os.environ:
        return os.environ.get('IWYU_BINARY')

    # TODO: Investigate using shutil.which when Python 2 has passed away.
    executable_name = 'include-what-you-use'
    if sys.platform.startswith('win'):
        executable_name += '.exe'

    search_path = [os.path.dirname(__file__)]
    search_path += os.environ.get('PATH', '').split(os.pathsep)

    for dirpath in search_path:
        full = os.path.join(dirpath, executable_name)
        if os.path.isfile(full):
            return os.path.realpath(full)

    return None


IWYU_EXECUTABLE = find_include_what_you_use()


class Process(object):
    """ Manages an IWYU process in flight """
    def __init__(self, proc, outfile, errfile=None):
        self.proc = proc
        self.outfile = outfile
        self.errfile = errfile
        self.output = None
        self.stderr = None

    def poll(self):
        """ Return the exit code if the process has completed, None otherwise.
        """
        return self.proc.poll()

    @property
    def returncode(self):
        return self.proc.returncode

    def _read_file(self, fileobj, errors='strict'):
        try:
            fileobj.seek(0)
            return fileobj.read().decode('utf-8', errors=errors)
        finally:
            fileobj.close()

    def get_output(self):
        """Return merged legacy output, or structured stdout."""
        if self.output is None:
            self.proc.wait()
            self.output = self._read_file(self.outfile)
        return self.output

    def get_stdout(self):
        """Return structured stdout after the child has completed."""
        return self.get_output()

    def get_stderr(self):
        """Return structured stderr after the child has completed."""
        if self.errfile is None:
            return ''
        if self.stderr is None:
            self.proc.wait()
            self.stderr = self._read_file(self.errfile, errors='replace')
        return self.stderr

    @classmethod
    def start(cls, invocation):
        """ Start a Process for the invocation and capture its output. """
        outfile = tempfile.TemporaryFile(prefix='iwyu')
        if invocation.structured_output:
            errfile = tempfile.TemporaryFile(prefix='iwyu-stderr')
            process = subprocess.Popen(
                invocation.command,
                cwd=invocation.cwd,
                stdout=outfile,
                stderr=errfile)
            return cls(process, outfile, errfile)

        process = subprocess.Popen(
            invocation.command,
            cwd=invocation.cwd,
            stdout=outfile,
            stderr=subprocess.STDOUT)
        return cls(process, outfile)


KNOWN_COMPILER_WRAPPERS=frozenset([
    "ccache"
])


class Invocation(object):
    """ Holds arguments of an IWYU invocation. """
    def __init__(self, command, cwd, database_index=None,
                 command_digest=None, invocation_id=None,
                 translation_unit=None, structured_output=False,
                 configuration=None, database_name=None,
                 database_local_index=None, output_schema_version=1):
        self.command = command
        self.cwd = cwd
        self.database_index = database_index
        self.command_digest = command_digest
        self.invocation_id = invocation_id
        self.translation_unit = translation_unit
        self.structured_output = structured_output
        self.configuration = configuration
        self.database_name = database_name
        self.database_local_index = database_local_index
        self.output_schema_version = output_schema_version

    def __str__(self):
        return ' '.join(self.command)

    @classmethod
    def from_compile_command(cls, entry, extra_args, database_index=None,
                             output_format=DEFAULT_FORMAT, configuration=None,
                             database_name=None, database_local_index=None,
                             output_schema_version=1):
        """ Parse a JSON compilation database entry into new Invocation. """
        if 'arguments' in entry:
            command = list(entry['arguments'])
        elif 'command' in entry:
            command = split_command(entry['command'])
        else:
            raise ValueError('Invalid compilation database entry: %s' % entry)
        if not command:
            raise ValueError('Empty compilation command: %s' % entry)
        command = _unwrap_compiler_wrapper(command)
        if not command:
            raise ValueError('Compilation command contains only a wrapper')

        structured_output = output_format == JSON_FORMAT
        if type(output_schema_version) is not int or output_schema_version not in (1, 2):
            raise ValueError('JSON output schema must be version 1 or 2')
        compile_command = command[0]
        windows_args = is_msvc_driver(compile_command)
        extra_args = list(extra_args)

        if not structured_output:
            if windows_args:
                # If the compiler is cl-compatible, let IWYU be cl-compatible.
                extra_args = ['--driver-mode=cl'] + extra_args
            return cls([IWYU_EXECUTABLE] + extra_args + command[1:],
                       entry['directory'])

        reserved_options = (NAMED_DATABASE_RESERVED_JSON_OPTIONS
                            if configuration is not None
                            else RESERVED_JSON_OPTIONS)
        reject_reserved_json_args(extra_args, reserved_options)

        if database_index is None:
            database_index = 0
        if 'file' not in entry:
            raise ValueError('JSON output requires a compilation file path')
        if not all(isinstance(argument, str) for argument in command):
            raise ValueError('compilation arguments must all be strings')

        real_directory = os.path.realpath(entry['directory'])
        source_file = entry['file']
        if not os.path.isabs(source_file):
            source_file = os.path.join(real_directory, source_file)
        directory = real_directory.replace(os.path.sep, '/')
        translation_unit = os.path.realpath(source_file).replace(os.path.sep,
                                                                  '/')
        canonical_args, windows_args = _canonical_compile_arguments(entry)
        reject_reserved_json_args(canonical_args[1:], reserved_options)
        if windows_args:
            extra_args = ['--driver-mode=cl'] + extra_args
        digest, invocation_id = command_identity(
            directory, translation_unit, canonical_args, database_index)
        protocol_args = [
            '-Xiwyu', '--output_format=json',
            '-Xiwyu', '--invocation_index=%d' % database_index,
            '-Xiwyu', '--command_digest=%s' % digest,
            '-Xiwyu', '--translation_unit=%s' % translation_unit,
        ]
        if output_schema_version != 1:
            protocol_args.extend([
                '-Xiwyu', '--output_schema=%d' % output_schema_version])
        if configuration is not None:
            validate_configuration_name(configuration)
            protocol_args.extend([
                '-Xiwyu', '--configuration=%s' % configuration])
        child_command = ([IWYU_EXECUTABLE] + extra_args + protocol_args +
                         canonical_args[1:])
        return cls(child_command, entry['directory'], database_index, digest,
                   invocation_id, translation_unit, structured_output=True,
                   configuration=configuration, database_name=database_name,
                   database_local_index=database_local_index,
                   output_schema_version=output_schema_version)

    def start(self, verbose):
        """ Run invocation and collect output. """
        if verbose:
            print('# %s' % self, file=sys.stderr)

        return Process.start(self)


def fixup_compilation_db(compilation_db):
    """ Canonicalize paths in JSON compilation database. """
    for entry in compilation_db:
        # Convert relative paths to absolute ones if possible, based on the
        # entry's directory.
        if 'directory' in entry and not os.path.isabs(entry['file']):
            entry['file'] = os.path.join(entry['directory'], entry['file'])

        # Expand relative paths and symlinks.
        entry['file'] = os.path.realpath(entry['file'])

    return compilation_db


def canonical_compilation_db_path(path):
    """Return the canonical compile_commands.json path for path."""
    if os.path.isdir(path):
        path = os.path.join(path, 'compile_commands.json')
    return os.path.realpath(path)


def _validate_compilation_db_entry(entry):
    if not isinstance(entry, dict):
        raise ValueError('compilation database entry is not an object')
    if not isinstance(entry.get('directory'), str) or not entry['directory']:
        raise ValueError('compilation database entry has invalid directory')
    if not isinstance(entry.get('file'), str) or not entry['file']:
        raise ValueError('compilation database entry has invalid file')
    has_arguments = 'arguments' in entry
    has_command = 'command' in entry
    if not has_arguments and not has_command:
        raise ValueError('compilation database entry has no command')
    if has_arguments and (not isinstance(entry['arguments'], list) or
                          not entry['arguments'] or
                          not all(isinstance(arg, str)
                                  for arg in entry['arguments'])):
        raise ValueError('compilation database entry has invalid arguments')
    if has_command and (not isinstance(entry['command'], str) or
                        not entry['command']):
        raise ValueError('compilation database entry has invalid command')


def load_compilation_db(path, require_nonempty=False):
    """Load and validate one compilation database from a canonical path."""
    canonical_path = canonical_compilation_db_path(path)
    try:
        with open(canonical_path, 'r', encoding='utf-8') as fileobj:
            compilation_db = json.load(
                fileobj, object_pairs_hook=_reject_duplicate_object_keys,
                parse_constant=_reject_nonfinite)
    except (IOError, UnicodeError, ValueError) as why:
        raise ValueError("failed to parse compilation database '%s': %s" %
                         (canonical_path, why))
    if not isinstance(compilation_db, list):
        raise ValueError("compilation database '%s' root is not an array" %
                         canonical_path)
    if require_nonempty and not compilation_db:
        raise ValueError("compilation database '%s' is empty" % canonical_path)
    for index, entry in enumerate(compilation_db):
        try:
            _validate_compilation_db_entry(entry)
        except ValueError as why:
            raise ValueError("compilation database '%s' entry %d: %s" %
                             (canonical_path, index, why))
    return canonical_path, fixup_compilation_db(compilation_db)


def slice_compilation_db(compilation_db, selection):
    """ Return a new compilation database reduced to the paths in selection. """
    if not selection:
        return compilation_db

    # Canonicalize selection paths to match compilation database.
    selection = [os.path.realpath(p) for p in selection]

    new_db = []
    for path in selection:
        if not os.path.exists(path):
            print('warning: \'%s\' not found on disk.' % path, file=sys.stderr)
            continue

        found = [e for e in compilation_db if is_subpath_of(e['file'], path)]
        if not found:
            print('warning: \'%s\' not found in compilation database.' % path,
                  file=sys.stderr)
            continue

        new_db.extend(found)

    return new_db


def execute(invocations, verbose, formatter, jobs, max_load_average=0):
    """ Launch processes described by invocations. """
    _validate_jobs(jobs)
    exit_code = 0
    if jobs == 1:
        for invocation in invocations:
            proc = invocation.start(verbose)
            print(formatter(proc.get_output()))
            exit_code = max(exit_code, proc.returncode)
        return exit_code

    pending = []
    while invocations or pending:
        # Collect completed IWYU processes and print results.
        complete = [proc for proc in pending if proc.poll() is not None]
        for proc in complete:
            pending.remove(proc)
            print(formatter(proc.get_output()))
            exit_code = max(exit_code, proc.returncode)

        # Schedule new processes if there's room.
        capacity = jobs - len(pending)

        if max_load_average > 0:
            one_min_load_average, _, _ = os.getloadavg()
            load_capacity = max_load_average - one_min_load_average
            if load_capacity < 0:
                load_capacity = 0
            if load_capacity < capacity:
                capacity = int(load_capacity)
                if not capacity and not pending:
                    # Ensure there is at least one job running.
                    capacity = 1

        pending.extend(i.start(verbose) for i in invocations[:capacity])
        invocations = invocations[capacity:]

        # Yield CPU.
        time.sleep(0.0001)
    return exit_code


class ProtocolError(ValueError):
    """A child violated the structured-output protocol."""


def _reject_duplicate_object_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON object key '%s'" % key)
        result[key] = value
    return result


_OUTPUT_V2_SCHEMA = None


def _output_v2_schema():
    global _OUTPUT_V2_SCHEMA
    if _OUTPUT_V2_SCHEMA is None:
        schema_path = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            'docs', 'output-v2.schema.json')
        try:
            with open(schema_path, 'r', encoding='utf-8') as schema_file:
                _OUTPUT_V2_SCHEMA = json.load(
                    schema_file, object_pairs_hook=_reject_duplicate_object_keys,
                    parse_constant=_reject_nonfinite)
        except (OSError, ValueError) as why:
            raise ProtocolError('cannot load output-v2 schema: %s' % why)
    return _OUTPUT_V2_SCHEMA


def _json_type_matches(value, expected):
    if expected == 'object':
        return isinstance(value, dict)
    if expected == 'array':
        return isinstance(value, list)
    if expected == 'string':
        return isinstance(value, str)
    if expected == 'integer':
        return type(value) is int
    if expected == 'boolean':
        return type(value) is bool
    if expected == 'null':
        return value is None
    return False


def _schema_path(path, component):
    return '%s.%s' % (path, component) if path else str(component)


def _validate_schema_value(value, schema, root, path='record'):
    """Validate the complete subset of draft 2020-12 used by output-v2."""
    if '$ref' in schema:
        reference = schema['$ref']
        if not reference.startswith('#/$defs/'):
            raise ProtocolError('%s: unsupported schema reference' % path)
        name = reference[len('#/$defs/'):]
        definition = root.get('$defs', {}).get(name)
        if definition is None:
            raise ProtocolError('%s: unresolved schema reference' % path)
        _validate_schema_value(value, definition, root, path)
        return

    if 'oneOf' in schema:
        matches = 0
        for alternative in schema['oneOf']:
            try:
                _validate_schema_value(value, alternative, root, path)
                matches += 1
            except ProtocolError:
                pass
        if matches != 1:
            raise ProtocolError('%s: does not match exactly one schema shape' % path)
        return

    if 'type' in schema and not _json_type_matches(value, schema['type']):
        raise ProtocolError('%s: expected %s' % (path, schema['type']))
    if 'const' in schema and value != schema['const']:
        raise ProtocolError('%s: value does not match schema constant' % path)
    if 'enum' in schema and value not in schema['enum']:
        raise ProtocolError('%s: value is outside schema enum' % path)

    if isinstance(value, dict):
        required = schema.get('required', [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ProtocolError('%s: missing required field %s' %
                                (path, missing[0]))
        properties = schema.get('properties', {})
        if schema.get('additionalProperties') is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise ProtocolError('%s: unexpected field %s' %
                                    (path, extras[0]))
        for key, child in value.items():
            if key in properties:
                _validate_schema_value(
                    child, properties[key], root, _schema_path(path, key))

    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0):
            raise ProtocolError('%s: array has too few items' % path)
        if schema.get('uniqueItems'):
            canonical = [_json_dumps_canonical(item) for item in value]
            if len(canonical) != len(set(canonical)):
                raise ProtocolError('%s: array items are not unique' % path)
        if 'items' in schema:
            for index, child in enumerate(value):
                _validate_schema_value(
                    child, schema['items'], root,
                    _schema_path(path, index))

    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0):
            raise ProtocolError('%s: string is too short' % path)
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            raise ProtocolError('%s: string does not match schema pattern' % path)

    if type(value) is int and 'minimum' in schema and value < schema['minimum']:
        raise ProtocolError('%s: integer is below schema minimum' % path)



def _normalized_output_path(path):
    if ('\\' in path or '//' in path or path.startswith('./') or
            (path != '/' and path.endswith('/'))):
        return False
    components = [part for part in path.split('/') if part]
    return (not any(part in ('.', '..') for part in components) and
            posixpath.normpath(path) == path)


def _use_sort_key(use):
    origin_rank = {'ast': 0, 'macro': 1, 'heuristic': 2, 'unknown': 3}
    requirement_rank = {
        'full': 0, 'forward_declaration': 1, 'none': 2, 'unknown': 3}
    location = use['location']
    return (location['path'], location['line'], location['column'],
            origin_rank[use['origin']], requirement_rank[use['requirement']],
            use['condition']['status'],
            _json_dumps_canonical(use['condition']),
            use['macro']['status'], _json_dumps_canonical(use['macro']))


def _known_location_in_file(availability, file_path):
    return (availability['status'] != 'known' or
            availability['value']['path'] == file_path)

def _validate_schema_v2(record):
    schema = _output_v2_schema()
    _validate_schema_value(record, schema, schema)


def _source_location_at_offset(source_bytes, path, offset):
    line_start = source_bytes.rfind(b'\n', 0, offset) + 1
    return {
        'path': path,
        'line': source_bytes.count(b'\n', 0, offset) + 1,
        'column': offset - line_start + 1,
    }


def _directive_body_begin_offset(source_bytes, offset):
    """Mirror IwyuPreprocessorInfo::DirectiveBodyBeginOffset exactly."""
    while offset < len(source_bytes):
        if source_bytes[offset] != ord('\n'):
            offset += 1
            continue
        slash = offset
        if slash > 0 and source_bytes[slash - 1] == ord('\r'):
            slash -= 1
        line_end = slash
        while slash > 0 and source_bytes[slash - 1] == ord('\\'):
            slash -= 1
        if (line_end - slash) % 2 == 0:
            return offset + 1
        offset += 1
    return len(source_bytes)


_CONDITIONAL_OPENERS = set([b'if', b'ifdef', b'ifndef'])
_CONDITIONAL_ALTERNATIVES = set([b'elif', b'elifdef', b'elifndef', b'else'])
_CONDITIONAL_DIRECTIVES = (
    _CONDITIONAL_OPENERS | _CONDITIONAL_ALTERNATIVES | set([b'endif']))
_HORIZONTAL_SPACE = b' \t\v\f\r'
_IDENTIFIER_BYTES = (
    b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')


def _phase2_splice(source_bytes):
    """Return phase-2 logical bytes and each byte's physical source offset."""
    logical = bytearray()
    physical = []
    index = 0
    while index < len(source_bytes):
        if source_bytes[index:index + 2] == b'\\\n':
            index += 2
            continue
        if source_bytes[index:index + 3] == b'\\\r\n':
            index += 3
            continue
        logical.append(source_bytes[index])
        physical.append(index)
        index += 1
    return bytes(logical), physical


def _raw_string_end(logical, index):
    """Return one-past a C++ raw string token, or None when this is not one."""
    match = re.match(br'(?:u8|u|U|L)?R"([^ ()\\\t\v\f\r\n]{0,16})\(',
                     logical[index:])
    if not match:
        return None
    delimiter = match.group(1)
    body = index + match.end()
    closing = b')' + delimiter + b'"'
    end = logical.find(closing, body)
    return None if end < 0 else end + len(closing)


def _directive_keyword_after_hash(logical, hash_index):
    index = hash_index + 1
    while index < len(logical):
        while index < len(logical) and logical[index] in _HORIZONTAL_SPACE:
            index += 1
        if logical[index:index + 2] == b'/*':
            end = logical.find(b'*/', index + 2)
            if end < 0 or b'\n' in logical[index:end + 2]:
                return None
            index = end + 2
            continue
        if logical[index:index + 2] == b'//' or (
                index < len(logical) and logical[index] == ord('\n')):
            return None
        break
    begin = index
    while index < len(logical) and logical[index] in _IDENTIFIER_BYTES:
        index += 1
    if begin == index:
        return None
    kind = logical[begin:index]
    return (kind, begin) if kind in _CONDITIONAL_DIRECTIVES else None


def _raw_lex_conditional_directives(source_bytes):
    """Independently lex conditional directives from exact physical bytes.

    This implements phase-2 backslash-newline splicing before recognizing
    comments and preprocessing lines. It tracks block comments, ordinary
    literals, and raw strings so directive-looking bytes inside them cannot
    enter the census.
    """
    logical, physical = _phase2_splice(source_bytes)
    directives = []
    index = 0
    at_line_start = True
    in_block_comment = False
    while index < len(logical):
        if in_block_comment:
            if logical[index:index + 2] == b'*/':
                in_block_comment = False
                index += 2
            else:
                if logical[index] == ord('\n'):
                    at_line_start = True
                index += 1
            continue

        if logical[index:index + 2] == b'//':
            newline = logical.find(b'\n', index + 2)
            if newline < 0:
                break
            index = newline
            continue
        if logical[index:index + 2] == b'/*':
            in_block_comment = True
            index += 2
            continue

        byte = logical[index]
        if byte == ord('\n'):
            at_line_start = True
            index += 1
            continue
        if at_line_start and byte in _HORIZONTAL_SPACE:
            index += 1
            continue
        if at_line_start and byte == ord('#'):
            found = _directive_keyword_after_hash(logical, index)
            if found is not None:
                kind, keyword_index = found
                directives.append({
                    'hash_offset': physical[index],
                    'offset': physical[keyword_index],
                    'body_begin': _directive_body_begin_offset(
                        source_bytes, physical[keyword_index]),
                    'kind': kind.decode('ascii'),
                })
            at_line_start = False
            index += 1
            continue

        raw_end = _raw_string_end(logical, index)
        if raw_end is not None:
            at_line_start = False
            index = raw_end
            continue
        if byte in (ord('"'), ord("'")):
            quote = byte
            at_line_start = False
            index += 1
            escaped = False
            while index < len(logical):
                current = logical[index]
                index += 1
                if escaped:
                    escaped = False
                elif current == ord('\\'):
                    escaped = True
                elif current == quote:
                    break
                elif current == ord('\n'):
                    # Invalid unspliced ordinary literals will be rejected by
                    # Clang. Resume line recognition to keep this lexer finite.
                    at_line_start = True
                    break
            continue

        at_line_start = False
        index += 1
    return directives


def _expected_conditional_regions(source_bytes):
    directives = _raw_lex_conditional_directives(source_bytes)
    templates = []
    stack = []
    for directive in directives:
        kind = directive['kind']
        if kind in ('if', 'ifdef', 'ifndef'):
            parent = (templates[stack[-1]['current']]['source_region_id']
                      if stack else None)
            source_id = 'branch-%d-0-%d' % (
                directive['offset'], directive['offset'])
            templates.append({
                'source_region_id': source_id,
                'opening_offset': directive['offset'],
                'directive_offset': directive['offset'],
                'body_begin_offset': directive['body_begin'],
                'body_end_offset': None,
                'branch_index': 0,
                'parent_source_region_id': parent,
                'directive_kind': kind,
            })
            stack.append({
                'opening_offset': directive['offset'],
                'branch_index': 0,
                'current': len(templates) - 1,
                'saw_else': False,
            })
        elif kind in ('elif', 'elifdef', 'elifndef', 'else'):
            if not stack or stack[-1]['saw_else']:
                raise ProtocolError('source bytes contain malformed conditional group')
            group = stack[-1]
            templates[group['current']]['body_end_offset'] = directive['offset']
            group['branch_index'] += 1
            group['saw_else'] = kind == 'else'
            parent = (templates[stack[-2]['current']]['source_region_id']
                      if len(stack) > 1 else None)
            source_id = 'branch-%d-%d-%d' % (
                group['opening_offset'], group['branch_index'],
                directive['offset'])
            templates.append({
                'source_region_id': source_id,
                'opening_offset': group['opening_offset'],
                'directive_offset': directive['offset'],
                'body_begin_offset': directive['body_begin'],
                'body_end_offset': None,
                'branch_index': group['branch_index'],
                'parent_source_region_id': parent,
                'directive_kind': kind,
            })
            group['current'] = len(templates) - 1
        else:
            if not stack:
                raise ProtocolError('source bytes contain unmatched #endif')
            templates[stack[-1]['current']]['body_end_offset'] = directive['offset']
            stack.pop()
    if stack:
        raise ProtocolError('source bytes contain unterminated conditional group')
    return templates


def _conditional_region_schema_sort_key(region):
    """Mirror the producer's conditional_regions schema ordering."""
    return (region['opening_offset'], region['branch_index'],
            region['directive_offset'], region.get('file_id', 0))


def _validate_conditional_regions(record, source_bytes):
    """Validate schema-v2 regions against the exact digest-checked bytes."""
    source_digest = record['source_digest']
    if record['file_digest'] != {'status': 'known', 'value': source_digest}:
        raise ProtocolError('file_digest differs from source_digest')

    file_path = record['file']['path']
    source_length = len(source_bytes)
    regions = record['conditional_regions']
    expected_regions = sorted(
        _expected_conditional_regions(source_bytes),
        key=_conditional_region_schema_sort_key)
    if bool(regions) != bool(expected_regions):
        raise ProtocolError(
            'conditional_regions is not a complete exact source census')
    regions_by_file_id = {}
    for region in regions:
        regions_by_file_id.setdefault(region['file_id'], []).append(region)
    for file_regions in regions_by_file_id.values():
        if len(file_regions) != len(expected_regions):
            raise ProtocolError(
                'conditional_regions is not a complete exact source census')
        for actual, expected in zip(file_regions, expected_regions):
            expected_parent = expected['parent_source_region_id']
            parent_availability = actual['parent_source_region_id']
            if expected_parent is None:
                parent = (None if parent_availability['status'] ==
                          'not_applicable' else '<invalid-availability>')
            else:
                parent = (parent_availability['value']
                          if parent_availability['status'] == 'known'
                          else '<invalid-availability>')
            actual_template = {
                'source_region_id': actual['source_region_id'],
                'opening_offset': actual['opening_offset'],
                'directive_offset': actual['directive_offset'],
                'body_begin_offset': actual['body_begin_offset'],
                'body_end_offset': (
                    actual['body_end_offset']['value']
                    if actual['body_end_offset']['status'] == 'known'
                    else None),
                'branch_index': actual['branch_index'],
                'parent_source_region_id': parent,
                'directive_kind': actual['directive_kind'],
            }
            if actual_template != expected:
                raise ProtocolError(
                    'conditional_regions differs from exact source census')
            expected_opening = _source_location_at_offset(
                source_bytes, file_path, expected['opening_offset'])
            expected_directive = _source_location_at_offset(
                source_bytes, file_path, expected['directive_offset'])
            if actual['opening_location'] != {
                    'status': 'known', 'value': expected_opening} or \
                    actual['directive_location'] != {
                        'status': 'known', 'value': expected_directive}:
                raise ProtocolError(
                    'conditional region location differs from exact source census')

    sort_keys = [
        _conditional_region_schema_sort_key(region) for region in regions]
    if sort_keys != sorted(sort_keys):
        raise ProtocolError('conditional regions are not sorted')

    identities = set()
    regions_by_identity = {}
    regions_by_source_id = {}
    groups = {}
    for region in regions:
        source_region_id = region['source_region_id']
        expected_id = 'branch-%d-%d-%d' % (
            region['opening_offset'], region['branch_index'],
            region['directive_offset'])
        if source_region_id != expected_id:
            raise ProtocolError('conditional region identity differs from offsets')
        identity = (region['file_id'], source_region_id)
        if identity in identities:
            raise ProtocolError('duplicate conditional region identity')
        identities.add(identity)
        regions_by_identity[identity] = region
        regions_by_source_id.setdefault(source_region_id, []).append(identity)
        groups.setdefault(
            (region['file_id'], region['opening_offset']), []).append(identity)

        opening = region['opening_offset']
        directive = region['directive_offset']
        body_begin = region['body_begin_offset']
        if not (0 <= opening < source_length and
                opening <= directive < body_begin <= source_length):
            raise ProtocolError('conditional region offsets are out of bounds or inconsistent')
        body_end = region['body_end_offset']
        if body_end['status'] == 'known':
            if not (body_begin <= body_end['value'] <= source_length):
                raise ProtocolError(
                    'conditional region body end is out of bounds or precedes body')

        if body_begin != _directive_body_begin_offset(source_bytes, directive):
            raise ProtocolError(
                'conditional region body begin differs from source directive')

        expected_locations = {
            'opening_location': _source_location_at_offset(
                source_bytes, file_path, opening),
            'directive_location': _source_location_at_offset(
                source_bytes, file_path, directive),
        }
        for location_name, expected_location in expected_locations.items():
            location = region[location_name]
            if (location['status'] == 'known' and
                    location['value'] != expected_location):
                raise ProtocolError(
                    'conditional region location differs from source offset')

        parent = region['parent_source_region_id']
        if parent['status'] not in ('known', 'not_applicable'):
            raise ProtocolError('conditional region parent availability is invalid')

    # Validate each complete #if/#elif/#else group and its exact source joins.
    for group_id, group_identities in groups.items():
        group = [regions_by_identity[identity]
                 for identity in group_identities]
        branch_indices = [region['branch_index'] for region in group]
        if branch_indices != list(range(len(group))):
            raise ProtocolError(
                'conditional region branch indices are not contiguous')
        first = group[0]
        if (first['directive_offset'] != first['opening_offset'] or
                first['directive_kind'] not in ('if', 'ifdef', 'ifndef')):
            raise ProtocolError('conditional region group has invalid opening branch')
        expected_parent = first['parent_source_region_id']
        expected_opening_location = first['opening_location']
        saw_else = False
        for index, region in enumerate(group):
            if (region['parent_source_region_id'] != expected_parent or
                    region['opening_location'] != expected_opening_location):
                raise ProtocolError('conditional region group metadata differs')
            kind = region['directive_kind']
            if index:
                if saw_else or kind not in ('elif', 'elifdef', 'elifndef',
                                             'else'):
                    raise ProtocolError('conditional region group has invalid branch shape')
                saw_else = kind == 'else'
            if index + 1 < len(group):
                following = group[index + 1]
                body_end = region['body_end_offset']
                if (body_end['status'] == 'known' and
                        body_end['value'] != following['directive_offset']):
                    raise ProtocolError(
                        'conditional region branch boundary differs from next directive')

    # Parent identity includes FileID. This makes the graph deterministic even
    # when one physical header is entered more than once.
    parent_by_identity = {}
    for identity, region in regions_by_identity.items():
        parent = region['parent_source_region_id']
        if parent['status'] == 'not_applicable':
            parent_by_identity[identity] = None
            continue
        parent_identity = (region['file_id'], parent['value'])
        parent_region = regions_by_identity.get(parent_identity)
        if parent_region is None:
            raise ProtocolError('conditional region parent is absent')
        parent_by_identity[identity] = parent_identity
        parent_end = parent_region['body_end_offset']
        child_end = region['body_end_offset']
        if parent_region['body_begin_offset'] > region['opening_offset']:
            raise ProtocolError('conditional region is outside parent body')
        if (parent_end['status'] == 'known' and
                child_end['status'] == 'known' and
                child_end['value'] > parent_end['value']):
            raise ProtocolError('conditional region body escapes parent body')
        if (parent_end['status'] == 'known' and
                region['opening_offset'] >= parent_end['value']):
            raise ProtocolError('conditional region is outside parent body')

    for identity in parent_by_identity:
        seen = set()
        current = identity
        while current is not None:
            if current in seen:
                raise ProtocolError('conditional region parent graph is cyclic')
            seen.add(current)
            current = parent_by_identity[current]

    def resolve_condition(condition, context, require_active,
                          require_file_scope):
        if condition['status'] != 'known':
            return
        value = condition['value']
        source_region_id = value['source_region_id']
        source_region_path = value['source_region_path']
        if source_region_path[-1] != source_region_id:
            raise ProtocolError('%s condition anchor differs from path' % context)

        candidates = [
            identity for identity in regions_by_source_id.get(
                source_region_path[0], [])
            if parent_by_identity[identity] is None]
        if not candidates:
            raise ProtocolError('%s condition path does not begin at a root' %
                                context)
        for child_source_id in source_region_path[1:]:
            next_candidates = []
            for child_identity in regions_by_source_id.get(child_source_id, []):
                if parent_by_identity[child_identity] in candidates:
                    next_candidates.append(child_identity)
            candidates = next_candidates
            if not candidates:
                raise ProtocolError('%s condition path has invalid parentage' %
                                    context)
        candidates = [identity for identity in candidates
                      if identity[1] == source_region_id]
        if not candidates:
            raise ProtocolError('%s condition path names an absent region' %
                                context)

        def resolved_path(identity):
            path = []
            current = identity
            while current is not None:
                path.append(current)
                current = parent_by_identity[current]
            path.reverse()
            return path

        location = value['location']
        if location['status'] == 'known':
            candidates = [
                identity for identity in candidates
                if regions_by_identity[identity]['directive_location'] == location]
            if not candidates:
                raise ProtocolError('%s condition location differs from anchor' %
                                    context)
        activity_candidates = []
        for identity in candidates:
            path = resolved_path(identity)
            all_active = all(
                regions_by_identity[item]['activity'] == 'active'
                for item in path)
            if all_active == value['active_branch']:
                activity_candidates.append((identity, path))
        if not activity_candidates:
            raise ProtocolError(
                '%s condition path activity is inconsistent with active_branch' %
                context)
        if require_active and not value['active_branch']:
            raise ProtocolError('%s condition anchor is not an active branch' %
                                context)
        if require_file_scope:
            file_scope = [
                (identity, path) for identity, path in activity_candidates
                if all(regions_by_identity[item]['lexical_scope'] == 'file'
                       for item in path)]
            if not file_scope:
                raise ProtocolError(
                    '%s condition path is not entirely active file scope' %
                    context)

    for operation in record['operations']:
        context = operation['id']
        # Only an applied removal edits the present include occurrence. Its
        # conditional anchor must therefore remain in active file scope.
        # Kept, reported, and suppressed removals are no-edit provenance and
        # may honestly originate in a function, class, namespace, or
        # declaration scope. The same is true of add and keep provenance;
        # consumers decide whether it describes a safe insertion site.
        require_file_scope = (
            operation['subject_action'] == 'remove' and
            operation['item_kind'] == 'include' and
            operation['disposition'] == 'apply')
        resolve_condition(operation['edit_condition'], context, True,
                          require_file_scope)
        multi_config = operation['multi_config']
        if multi_config['status'] == 'known':
            for contribution in multi_config['value']:
                if contribution['subject_action'] != 'none':
                    resolve_condition(
                        contribution['condition'], context + ' contribution',
                        True, False)
        for symbol in operation['symbols']:
            for use in symbol['uses']:
                # Uses inside functions, classes, and other non-file scopes are
                # valid provenance. They still must name a branch active in
                # at least one matching physical include instance.
                resolve_condition(use['condition'], context + ' use', False,
                                  False)


def _declaration_kinds_incompatible(kinds):
    return len(kinds) > 1 and not kinds <= set(['class', 'struct'])



def _skip_include_trivia(spelling, index):
    """Skip horizontal whitespace and complete block comments."""
    while index < len(spelling):
        if spelling[index] in ' \t\v\f\r':
            index += 1
            continue
        if spelling.startswith('/*', index):
            end = spelling.find('*/', index + 2)
            if end < 0 or '\n' in spelling[index:end + 2]:
                raise ProtocolError('include spelling has an unterminated comment')
            index = end + 2
            continue
        break
    return index


_INCLUDE_PP_PUNCTUATORS = tuple(sorted((
    '%:%:', '>>=', '<<=', '<=>', '...', '::', '->*', '.*', '->', '++',
    '--', '<<', '>>', '<=', '>=', '==', '!=', '&&', '||', '+=', '-=',
    '*=', '/=', '%=', '^=', '&=', '|=', '##', '<:', ':>', '<%', '%>', '%:',
    '(', ')', '[', ']', '{', '}', '<', '>', '.', ',', ';', '?', ':', '~',
    '!', '+', '-', '*', '/', '%', '^', '&', '|', '=', '#'),
    key=len, reverse=True))
_INCLUDE_UNSAFE_PUNCTUATORS = frozenset(
    ('{', '}', ';', '#', '##', '<%', '%>', '%:', '%:%:'))


def _computed_include_tokens(spelling, index):
    """Return a safe, comment/whitespace-independent pp-token sequence."""
    tokens = []
    delimiters = []
    while index < len(spelling):
        char = spelling[index]
        if char in ' \t\v\f\r':
            index += 1
            continue
        if spelling.startswith('//', index):
            index = len(spelling)
            break
        if spelling.startswith('/*', index):
            end = spelling.find('*/', index + 2)
            if end < 0 or '\n' in spelling[index:end + 2]:
                raise ProtocolError(
                    'include spelling has an unterminated comment')
            index = end + 2
            continue
        if char.isalpha() or char == '_' or ord(char) >= 128:
            end = index + 1
            while end < len(spelling):
                candidate = spelling[end]
                if not (candidate.isalnum() or candidate == '_' or
                        ord(candidate) >= 128):
                    break
                end += 1
            tokens.append(spelling[index:end])
            index = end
            continue
        if char.isdigit() or (char == '.' and index + 1 < len(spelling) and
                              spelling[index + 1].isdigit()):
            end = index + 1
            while end < len(spelling):
                candidate = spelling[end]
                previous = spelling[end - 1]
                if (candidate.isalnum() or candidate in "._'" or
                        (candidate in '+-' and previous in 'eEpP')):
                    end += 1
                    continue
                break
            tokens.append(spelling[index:end])
            index = end
            continue
        if char in "'\"":
            quote = char
            end = index + 1
            escaped = False
            while end < len(spelling):
                candidate = spelling[end]
                end += 1
                if escaped:
                    escaped = False
                elif candidate == '\\':
                    escaped = True
                elif candidate == quote:
                    break
            else:
                raise ProtocolError(
                    'include spelling has an unterminated literal')
            tokens.append(spelling[index:end])
            index = end
            continue
        token = next((candidate for candidate in _INCLUDE_PP_PUNCTUATORS
                      if spelling.startswith(candidate, index)), None)
        if token is None or token in _INCLUDE_UNSAFE_PUNCTUATORS:
            raise ProtocolError(
                'include spelling has an unsafe preprocessing token')
        if token in ('(', '[', '<:'):
            delimiters.append(')' if token == '(' else ']')
        elif token in (')', ']', ':>'):
            closing = ']' if token == ':>' else token
            if not delimiters or delimiters.pop() != closing:
                raise ProtocolError('include spelling has unbalanced delimiters')
        tokens.append(token)
        index += len(token)
    if not tokens:
        raise ProtocolError('include spelling has an empty computed operand')
    first = tokens[0]
    if not (first[0].isalpha() or first[0] == '_' or ord(first[0]) >= 128):
        raise ProtocolError(
            'computed include operand does not begin with a macro identifier')
    if delimiters:
        raise ProtocolError('include spelling has unbalanced delimiters')
    return tuple(tokens)


def _logical_include_token(spelling):
    """Return a typed literal token or opaque computed-operand identity."""
    spelling = re.sub(r'\\\r?\n', '', spelling)
    if '\n' in spelling:
        raise ProtocolError('include spelling is not one logical line')
    index = _skip_include_trivia(spelling, 0)
    if index >= len(spelling) or spelling[index] != '#':
        raise ProtocolError('include spelling does not begin with a directive')
    index = _skip_include_trivia(spelling, index + 1)
    if not spelling.startswith('include', index):
        raise ProtocolError('include spelling does not name include')
    index += len('include')
    if index < len(spelling) and (spelling[index].isalnum() or
                                 spelling[index] == '_'):
        raise ProtocolError('include spelling has a longer directive name')
    index = _skip_include_trivia(spelling, index)
    if index >= len(spelling):
        raise ProtocolError('include spelling has no header operand')
    if spelling[index] in '<"':
        closing = '>' if spelling[index] == '<' else '"'
        end = spelling.find(closing, index + 1)
        if end < 0 or end == index + 1:
            raise ProtocolError('include spelling has an invalid header token')
        token = spelling[index:end + 1]
        index = _skip_include_trivia(spelling, end + 1)
        if spelling.startswith('//', index):
            index = len(spelling)
        if index != len(spelling):
            raise ProtocolError('include spelling has trailing non-comment text')
        return ('literal', token)
    return ('computed', _computed_include_tokens(spelling, index))


def _logical_include_spelling(item):
    """Return canonical literal spelling; computed spelling needs producer evidence."""
    identity = _logical_include_token(item['spelling'])
    if identity[0] != 'literal':
        raise ProtocolError(
            'cannot derive analyzer canonical line for computed include')
    return '#include ' + identity[1]


_CPP_MULTI_TOKENS = tuple(sorted((
    '>>=', '<<=', '<=>', '...', '::', '->*', '.*', '->', '++', '--',
    '<<', '>>', '<=', '>=', '==', '!=', '&&', '||', '+=', '-=', '*=',
    '/=', '%=', '^=', '&=', '|=', '##'), key=len, reverse=True))


def _forward_declaration_tokens(spelling):
    """Return a comment/whitespace-independent physical C++ token identity."""
    tokens = []
    index = 0
    while index < len(spelling):
        char = spelling[index]
        if char.isspace():
            index += 1
            continue
        if spelling.startswith('//', index):
            newline = spelling.find('\n', index + 2)
            index = len(spelling) if newline < 0 else newline + 1
            continue
        if spelling.startswith('/*', index):
            end = spelling.find('*/', index + 2)
            if end < 0:
                raise ProtocolError(
                    'forward-declaration spelling has an unterminated comment')
            index = end + 2
            continue
        if char.isalpha() or char == '_' or ord(char) >= 128:
            end = index + 1
            while end < len(spelling):
                candidate = spelling[end]
                if not (candidate.isalnum() or candidate == '_' or
                        ord(candidate) >= 128):
                    break
                end += 1
            tokens.append(spelling[index:end])
            index = end
            continue
        if char.isdigit():
            end = index + 1
            while end < len(spelling) and (
                    spelling[end].isalnum() or spelling[end] in "._'"):
                end += 1
            tokens.append(spelling[index:end])
            index = end
            continue
        if char in "'\"":
            quote = char
            end = index + 1
            escaped = False
            while end < len(spelling):
                current = spelling[end]
                end += 1
                if escaped:
                    escaped = False
                elif current == '\\':
                    escaped = True
                elif current == quote:
                    break
                elif current == '\n':
                    raise ProtocolError(
                        'forward-declaration spelling has an unterminated literal')
            else:
                raise ProtocolError(
                    'forward-declaration spelling has an unterminated literal')
            tokens.append(spelling[index:end])
            index = end
            continue
        token = next((candidate for candidate in _CPP_MULTI_TOKENS
                      if spelling.startswith(candidate, index)), char)
        tokens.append(token)
        index += len(token)
    if not tokens:
        raise ProtocolError('forward-declaration spelling has no C++ tokens')
    return tuple(tokens)


def _forward_declaration_core(tokens):
    """Remove context/attributes but preserve the complete declaration shape."""
    try:
        semicolon = tokens.index(';')
    except ValueError:
        return None
    if any(token != '}' for token in tokens[semicolon + 1:]):
        return None
    body = list(tokens[:semicolon + 1])
    stripped = []
    index = 0
    while index < len(body):
        token = body[index]
        if token in ('__attribute__', '__declspec', 'alignas') and (
                index + 1 < len(body) and body[index + 1] == '('):
            depth = 0
            index += 1
            while index < len(body):
                if body[index] == '(':
                    depth += 1
                elif body[index] == ')':
                    depth -= 1
                    if depth == 0:
                        index += 1
                        break
                index += 1
            if depth != 0:
                return None
            continue
        if token == '[' and index + 1 < len(body) and body[index + 1] == '[':
            index += 2
            depth = 1
            while index < len(body):
                if body[index:index + 2] == ['[', '[']:
                    depth += 1
                    index += 2
                elif body[index:index + 2] == [']', ']']:
                    depth -= 1
                    index += 2
                    if depth == 0:
                        break
                else:
                    index += 1
            if depth != 0:
                return None
            continue
        stripped.append(token)
        index += 1
    semicolon = stripped.index(';')
    kind_indices = [index for index, token in enumerate(stripped[:semicolon])
                    if token in ('class', 'struct', 'union', 'enum')]
    if not kind_indices:
        return None
    kind_index = kind_indices[-1]
    if (stripped[kind_index] in ('class', 'struct') and kind_index > 0 and
            stripped[kind_index - 1] == 'enum'):
        kind_index -= 1
    name_index = kind_index + 1
    if (stripped[kind_index] == 'enum' and name_index < semicolon and
            stripped[name_index] in ('class', 'struct')):
        name_index += 1
    if name_index >= semicolon:
        return None
    while name_index + 2 < semicolon and stripped[name_index + 1] == '::':
        name_index += 2
    name = stripped[name_index]
    if not (name[0].isalpha() or name[0] == '_' or ord(name[0]) >= 128):
        return None
    template_indices = [index for index, token in enumerate(stripped[:kind_index])
                        if token == 'template']
    prefix = stripped[template_indices[-1]:kind_index] if template_indices else []
    kind_tokens = stripped[kind_index:name_index]
    while len(kind_tokens) >= 2 and kind_tokens[-1] == '::':
        kind_tokens = kind_tokens[:-2]
    return tuple(prefix + kind_tokens + [name] +
                 stripped[name_index + 1:semicolon + 1])


def _forward_desired_index(items):
    index = {}
    for item in items:
        if item['item_kind'] != 'forward_declaration':
            continue
        tokens = _forward_declaration_tokens(item['spelling'])
        core = _forward_declaration_core(tokens)
        if core is None:
            raise ProtocolError(
                'canonical forward declaration has no stable core')
        index[core] = tokens if core not in index else None
    return index


def _logical_item_key(item, forward_desired=None):
    """Derive schema-v2 final-state identity without changing wire shape."""
    kind = item['item_kind']
    spelling = item['spelling']
    if kind == 'include':
        return (kind, _logical_include_token(spelling))
    tokens = _forward_declaration_tokens(spelling)
    if forward_desired is not None:
        core = _forward_declaration_core(tokens)
        canonical = forward_desired.get(core) if core is not None else None
        if canonical is not None:
            tokens = canonical
        elif 'source_range' in item:
            tokens = ('<unmatched>',) + tokens + (
                _json_dumps_canonical(item['source_range']),)
    return (kind, tokens)

def _validate_allow_remove_operation(operation, desired, operations):
    if operation['reason_code'] != 'project_policy_allow_remove':
        return
    if (operation['subject_action'] != 'remove' or
            operation['item_kind'] != 'include' or
            operation['disposition'] != 'apply' or
            operation['source_range']['status'] != 'known'):
        raise ProtocolError(
            'project_policy_allow_remove is not an applied physical include removal')
    policy = operation['policy']
    if (policy['status'] != 'known' or
            policy['value']['decision'] != 'allow'):
        raise ProtocolError(
            'project_policy_allow_remove lacks known allow provenance')
    if any(value['status'] != 'not_applicable'
           for value in operation['provider'].values()):
        raise ProtocolError(
            'project_policy_allow_remove has applicable provider evidence')
    if _logical_include_token(operation['spelling'])[0] != 'literal':
        raise ProtocolError(
            'project_policy_allow_remove does not name a literal include')
    symbols = operation['symbols']
    if not symbols:
        raise ProtocolError(
            'project_policy_allow_remove lacks replacement symbols')
    replacement_names = set()
    for symbol in symbols:
        if (symbol['qualified_name']['status'] != 'known' or
                symbol['declaration']['status'] != 'known' or
                symbol['declaration_kind']['status'] != 'known' or
                not symbol['uses'] or
                any(use['requirement'] != 'forward_declaration'
                    for use in symbol['uses']) or
                any(use['condition'] != operation['edit_condition']
                    for use in symbol['uses'])):
            raise ProtocolError(
                'project_policy_allow_remove has invalid forward replacement symbol')
        replacement_names.add(symbol['qualified_name']['value'])
    if (desired['status'] != 'known' or not desired['complete']):
        raise ProtocolError(
            'project_policy_allow_remove lacks complete desired state')
    desired_include_tokens = {
        _logical_include_token(item['spelling'])
        for item in desired['items'] if item['item_kind'] == 'include'}
    include_token = _logical_include_token(operation['spelling'])
    if include_token in desired_include_tokens:
        retained_occurrence = any(
            other is not operation and other['item_kind'] == 'include' and
            _logical_include_token(other['spelling']) == include_token and
            other['source_range']['status'] == 'known' and
            _json_dumps_canonical(other['source_range']) !=
                _json_dumps_canonical(operation['source_range']) and
            ((other['subject_action'] == 'remove' and
              other['disposition'] != 'apply') or
             other['subject_action'] == 'keep')
            for other in operations)
        if not retained_occurrence:
            raise ProtocolError(
                'project_policy_allow_remove include remains in desired state')
    desired_forward_names = {
        name for item in desired['items']
        if item['item_kind'] == 'forward_declaration'
        for name in item['symbols']}
    if not replacement_names <= desired_forward_names:
        raise ProtocolError(
            'project_policy_allow_remove replacement is absent from desired forwards')

def _validate_declaration_facts(record, source_bytes, native_conflicts=True,
                                schema_validated=False):
    if not schema_validated:
        _validate_schema_v2(record)
    _validate_conditional_regions(record, source_bytes)

    facts = record['declaration_facts']
    fact_keys = []
    facts_by_name = {}
    for fact in facts:
        key = (fact['qualified_name'], fact['kind'])
        fact_keys.append(key)
        if not _normalized_output_path(fact['declaration']['path']):
            raise ProtocolError('declaration fact path is not normalized')
        facts_by_name.setdefault(fact['qualified_name'], set()).add(
            (fact['kind'], _json_dumps_canonical(fact['declaration'])))
    if len(fact_keys) != len(set(fact_keys)):
        raise ProtocolError('duplicate schema-v2 declaration fact')
    if fact_keys != sorted(fact_keys):
        raise ProtocolError('schema-v2 declaration facts are not sorted')
    for qualified_name, declarations in facts_by_name.items():
        kinds = set(kind for kind, _ in declarations)
        if _declaration_kinds_incompatible(kinds):
            raise ProtocolError(
                'native declaration facts contain incompatible kind families '
                'for %s' % qualified_name)
    if native_conflicts and record['declaration_conflicts'] != []:
        raise ProtocolError(
            'native schema-v2 declaration_conflicts must be an empty array')
    if not native_conflicts:
        conflict_names = []
        for conflict in record['declaration_conflicts']:
            if conflict['source_digest'] != record['source_digest']:
                raise ProtocolError('declaration conflict source_digest differs')
            conflict_names.append(conflict['qualified_name'])
            declarations = conflict['declarations']
            declaration_keys = [(item['configuration'], item['kind'])
                                for item in declarations]
            if (len(declaration_keys) != len(set(declaration_keys)) or
                    declaration_keys != sorted(declaration_keys)):
                raise ProtocolError(
                    'conflicting declarations are duplicate or unsorted')
            configurations = set(item['configuration'] for item in declarations)
            kinds = set(item['kind'] for item in declarations)
            if (len(configurations) < 2 or len(kinds) < 2 or
                    kinds <= set(['class', 'struct'])):
                raise ProtocolError(
                    'declaration conflict does not describe incompatible kinds')
        if (len(conflict_names) != len(set(conflict_names)) or
                conflict_names != sorted(conflict_names)):
            raise ProtocolError('declaration conflicts are duplicate or unsorted')

    file_path = record['file']['path']
    if not _normalized_output_path(file_path):
        raise ProtocolError('file.path is not normalized')
    analyzed = record['analyzed_configurations']
    selected_configuration = record['invocation']['configuration']
    if analyzed['status'] == 'known':
        configurations = analyzed['value']
        if (not configurations or
                len(configurations) != len(set(configurations)) or
                configurations != sorted(configurations)):
            raise ProtocolError(
                'analyzed configurations are empty, duplicate, or unsorted')
        if (selected_configuration['status'] == 'known' and
                selected_configuration['value'] not in configurations):
            raise ProtocolError(
                'selected configuration is outside analyzed configurations')
    operations = record['operations']
    desired = record['desired_state']
    status = record['status']
    diagnostics = record['diagnostics']
    applied = [operation for operation in operations
               if operation['disposition'] == 'apply' and
               operation['subject_action'] in ('add', 'remove')]
    if status == 'correct' and applied:
        raise ProtocolError('correct record contains an applied edit')
    if status == 'changes' and not applied:
        raise ProtocolError('changes record contains no applied edit')
    if status in ('skipped', 'error') and applied:
        raise ProtocolError('%s record contains an applied edit' % status)
    if status == 'skipped' and not diagnostics:
        raise ProtocolError('skipped record contains no diagnostic')
    if status == 'error' and not any(
            diagnostic['severity'] == 'error' for diagnostic in diagnostics):
        raise ProtocolError('error record contains no error diagnostic')
    if status in ('correct', 'changes') and (
            desired['status'] != 'known' or not desired['complete']):
        raise ProtocolError('%s record lacks complete desired state' % status)

    operation_ids = [operation['id'] for operation in operations]
    expected_ids = ['op-%d' % index
                    for index in range(1, len(operations) + 1)]
    if operation_ids != expected_ids:
        raise ProtocolError('operation IDs are not sequential in output order')
    if operations != sorted(operations, key=_operation_sort_key):
        raise ProtocolError('operations are not deterministically sorted')
    if record['schema_version'] == 2:
        identities = [
            (operation['subject_action'], operation['item_kind'],
             operation['spelling'],
             _json_dumps_canonical(operation['source_range']),
             _json_dumps_canonical(operation['edit_condition']))
            for operation in operations]
    else:
        # Version 1 cannot use condition provenance as occurrence identity.
        identities = [
            (operation['subject_action'], operation['item_kind'],
             operation['spelling'],
             _json_dumps_canonical(operation['source_range']))
            for operation in operations]
    if len(identities) != len(set(identities)):
        raise ProtocolError('duplicate semantic operation identity')

    if record['schema_version'] == 2:
        present_occurrences = {}
        for operation in operations:
            if operation['subject_action'] not in ('remove', 'keep'):
                continue
            source_range_identity = _json_dumps_canonical(
                operation['source_range'])
            occurrence = (
                operation['item_kind'], operation['spelling'],
                source_range_identity)
            if operation['source_range']['status'] != 'known':
                occurrence += (_json_dumps_canonical(
                    operation['edit_condition']),)
            previous = present_occurrences.get(occurrence)
            if previous is not None:
                raise ProtocolError(
                    '%s and %s ambiguously identify the same present occurrence' %
                    (previous, operation['id']))
            present_occurrences[occurrence] = operation['id']

    associations = record['associated_headers']
    association_keys = [(association['path'], association['relationship'],
                         association['reason_code'])
                        for association in associations]
    if (len(association_keys) != len(set(association_keys)) or
            association_keys != sorted(association_keys)):
        raise ProtocolError('associated headers are duplicate or unsorted')
    if any(not _normalized_output_path(association['path'])
           for association in associations):
        raise ProtocolError('associated header path is not normalized')

    allowed = {
        'add': set(['apply', 'suppress', 'report']),
        'remove': set(['apply', 'keep', 'suppress', 'report']),
        'keep': set(['keep', 'report']),
    }
    policy_disposition = {
        'allow': 'apply', 'deny': 'suppress',
        'keep': 'keep', 'report': 'report',
    }
    for operation in operations:
        subject = operation['subject_action']
        disposition = operation['disposition']
        if disposition not in allowed[subject]:
            raise ProtocolError('%s has invalid subject/disposition pair' %
                                operation['id'])
        source_range = operation['source_range']
        if subject == 'add' and source_range['status'] != 'not_applicable':
            raise ProtocolError('%s add range is not_applicable' % operation['id'])
        if subject in ('remove', 'keep') and \
                source_range['status'] == 'not_applicable':
            raise ProtocolError('%s present range is not_applicable' %
                                operation['id'])
        if source_range['status'] == 'known':
            start = source_range['value']['start']
            end = source_range['value']['end']
            if (start['path'] != file_path or end['path'] != file_path or
                    (end['line'], end['column']) <
                    (start['line'], start['column'])):
                raise ProtocolError('%s has inconsistent source range' %
                                    operation['id'])

        symbols = operation['symbols']
        symbol_keys = []
        for symbol in symbols:
            qualified = symbol['qualified_name']
            identity = (qualified['value'] if qualified['status'] == 'known'
                        else symbol['name'])
            symbol_keys.append((identity, symbol['name']))
            declaration = symbol['declaration']
            kind = symbol['declaration_kind']
            if qualified['status'] == 'known' and kind['status'] == 'known':
                candidates = facts_by_name.get(qualified['value'], set())
                if declaration['status'] == 'known':
                    expected = (kind['value'],
                                _json_dumps_canonical(declaration['value']))
                    if candidates and expected not in candidates:
                        raise ProtocolError(
                            '%s symbol kind/location contradicts declaration fact' %
                            operation['id'])
            use_keys = []
            for use in symbol['uses']:
                location = use['location']
                if location['path'] != file_path:
                    raise ProtocolError('%s symbol use is outside analyzed file' %
                                        operation['id'])
                use_keys.append(_use_sort_key(use))
                macro = use['macro']
                if (use['origin'] == 'macro' and
                        macro['status'] == 'not_applicable'):
                    raise ProtocolError('%s macro use lacks provenance' %
                                        operation['id'])
                if use['origin'] != 'macro' and macro['status'] == 'known':
                    raise ProtocolError('%s non-macro use has macro provenance' %
                                        operation['id'])
                if macro['status'] == 'known':
                    expansion = macro['value']['expansion']
                    if (expansion['status'] == 'known' and
                            expansion['value'] != location):
                        raise ProtocolError('%s macro expansion differs from use' %
                                            operation['id'])
                condition = use['condition']
                if (condition['status'] == 'known' and
                        not _known_location_in_file(
                            condition['value']['location'], file_path)):
                    raise ProtocolError('%s use condition is outside file' %
                                        operation['id'])
            if (len(use_keys) != len(set(use_keys)) or
                    use_keys != sorted(use_keys)):
                raise ProtocolError('%s symbol uses are duplicate or unsorted' %
                                    operation['id'])
        if (len(symbol_keys) != len(set(symbol_keys)) or
                symbol_keys != sorted(symbol_keys)):
            raise ProtocolError('%s symbols are duplicate or unsorted' %
                                operation['id'])

        provider = operation['provider']
        selected = provider['selected']
        candidates = provider['candidates']
        chain = provider['chain']
        provider_refs = []
        if selected['status'] == 'known':
            provider_refs.append(selected['value'])
        if candidates['status'] == 'known':
            provider_refs.extend(candidates['value'])
        for provider_ref in provider_refs:
            if (not re.match(r'^(?:<[^>]+>|"[^"]+")$',
                             provider_ref['spelling']) or
                    not _normalized_output_path(provider_ref['path'])):
                raise ProtocolError('%s has invalid provider reference' %
                                    operation['id'])
        if (chain['status'] == 'known' and
                any(not _normalized_output_path(path)
                    for path in chain['value'])):
            raise ProtocolError('%s has non-normalized provider chain' %
                                operation['id'])
        if candidates['status'] == 'known':
            candidate_keys = [(item['path'], item['spelling'])
                              for item in candidates['value']]
            if (len(candidate_keys) != len(set(candidate_keys)) or
                    candidate_keys != sorted(candidate_keys)):
                raise ProtocolError('%s provider candidates are duplicate or unsorted' %
                                    operation['id'])
            if (selected['status'] == 'known' and
                    selected['value'] not in candidates['value']):
                raise ProtocolError('%s selected provider is absent from candidates' %
                                    operation['id'])
        if selected['status'] == 'known' and operation['item_kind'] == 'include':
            include_identity = _logical_include_token(operation['spelling'])
            if (include_identity[0] == 'literal' and
                    include_identity[1] != selected['value']['spelling']):
                raise ProtocolError('%s selected provider contradicts include' %
                                    operation['id'])
        if chain['status'] == 'known':
            if (selected['status'] != 'known' or not chain['value'] or
                    chain['value'][0] != file_path or
                    chain['value'][-1] != selected['value']['path']):
                raise ProtocolError('%s provider chain is inconsistent' %
                                    operation['id'])

        edit_condition = operation['edit_condition']
        if (edit_condition['status'] == 'known' and
                not _known_location_in_file(
                    edit_condition['value']['location'], file_path)):
            raise ProtocolError('%s edit condition is outside file' %
                                operation['id'])
        policy = operation['policy']
        if (policy['status'] == 'known' and
                policy_disposition[policy['value']['decision']] != disposition):
            raise ProtocolError('%s policy contradicts disposition' %
                                operation['id'])
        _validate_allow_remove_operation(operation, desired, operations)
        multi = operation['multi_config']
        if (analyzed['status'] == 'known' and
                len(analyzed['value']) > 1 and multi['status'] != 'known'):
            raise ProtocolError('%s lacks multi-config contributions' %
                                operation['id'])
        if multi['status'] == 'known':
            contributions = multi['value']
            configurations = [item['configuration'] for item in contributions]
            if (len(configurations) != len(set(configurations)) or
                    configurations != sorted(configurations)):
                raise ProtocolError('%s contributions are duplicate or unsorted' %
                                    operation['id'])
            if (analyzed['status'] != 'known' or
                    configurations != analyzed['value']):
                raise ProtocolError('%s contribution coverage differs' %
                                    operation['id'])
            for contribution in contributions:
                none = contribution['subject_action'] == 'none'
                if none != (contribution['disposition'] == 'not_applicable'):
                    raise ProtocolError('%s contribution is inconsistent' %
                                        operation['id'])
                if not none and contribution['subject_action'] != subject:
                    raise ProtocolError('%s contribution subject differs' %
                                        operation['id'])
                contribution_condition = contribution['condition']
                if (contribution_condition['status'] == 'known' and
                        not _known_location_in_file(
                            contribution_condition['value']['location'],
                            file_path)):
                    raise ProtocolError('%s contribution condition is outside file' %
                                        operation['id'])
                if none and contribution_condition['status'] != 'not_applicable':
                    raise ProtocolError('%s none contribution has a condition' %
                                        operation['id'])
                if not none and contribution_condition != edit_condition:
                    raise ProtocolError('%s contribution condition differs' %
                                        operation['id'])
            if disposition in ('apply', 'keep', 'suppress') and any(
                    item['subject_action'] != subject or
                    item['disposition'] != disposition
                    for item in contributions):
                raise ProtocolError('%s contribution disposition differs' %
                                    operation['id'])
            if disposition == 'report' and not any(
                    item['disposition'] == 'report'
                    for item in contributions):
                raise ProtocolError('%s reported operation lacks report contribution' %
                                    operation['id'])

    if desired['status'] == 'known':
        if record['schema_version'] == 1:
            desired_keys = [(item['item_kind'], item['spelling'])
                            for item in desired['items']]
        else:
            forward_desired = _forward_desired_index(desired['items'])
            desired_keys = [_logical_item_key(item, forward_desired)
                            for item in desired['items']]
        if len(desired_keys) != len(set(desired_keys)):
            raise ProtocolError('duplicate desired-state item')
        seen_forward_declaration = False
        for item in desired['items']:
            if item['item_kind'] == 'forward_declaration':
                seen_forward_declaration = True
            elif seen_forward_declaration:
                raise ProtocolError(
                    'desired-state include follows a forward declaration')
            if (item['symbols'] != sorted(item['symbols']) or
                    len(item['symbols']) != len(set(item['symbols']))):
                raise ProtocolError(
                    'desired-state symbols are duplicate or unsorted')
        desired_set = set(desired_keys)
        if record['schema_version'] == 1:
            # Version 1 has no occurrence identity. Mixed removal/retention or
            # remove/add relocation for one key remains unrepresentable.
            for operation in operations:
                key = (operation['item_kind'], operation['spelling'])
                present = ((operation['subject_action'] == 'add' and
                            operation['disposition'] == 'apply') or
                           (operation['subject_action'] == 'remove' and
                            operation['disposition'] != 'apply') or
                           operation['subject_action'] == 'keep')
                if present != (key in desired_set):
                    raise ProtocolError('%s contradicts desired state' %
                                        operation['id'])
        else:
            operations_by_key = {}
            for operation in operations:
                key = _logical_item_key(operation, forward_desired)
                operations_by_key.setdefault(key, []).append(operation)
            for key, key_operations in operations_by_key.items():
                # Presence after all dispositions is established by an applied
                # addition or a present occurrence that is retained. An
                # applied removal affects only its identified occurrence.
                presence_witnesses = [
                    operation for operation in key_operations
                    if ((operation['subject_action'] == 'add' and
                         operation['disposition'] == 'apply') or
                        (operation['subject_action'] == 'remove' and
                         operation['disposition'] != 'apply') or
                        operation['subject_action'] == 'keep')]
                desired_present = key in desired_set
                computed_include = (
                    key[0] == 'include' and key[1][0] == 'computed')
                if computed_include:
                    applied_adds = [
                        operation for operation in key_operations
                        if operation['subject_action'] == 'add' and
                        operation['disposition'] == 'apply']
                    if applied_adds and any(
                            operation['spelling'] not in {
                                item['spelling'] for item in desired['items']
                                if item['item_kind'] == 'include'}
                            for operation in applied_adds):
                        operation_ids = ', '.join(
                            operation['id'] for operation in applied_adds)
                        raise ProtocolError(
                            '%s computed add does not match analyzer canonical '
                            'desired line' % operation_ids)
                    # A present computed directive is serialized physically in
                    # the operation, while the analyzer's desired state names
                    # its expanded canonical literal. Without a producer field
                    # linking those spellings, absence of the opaque key is not
                    # evidence of absence. A computed desired key still cannot
                    # survive a group with no presence witness.
                    if desired_present and not presence_witnesses:
                        operation_ids = ', '.join(
                            operation['id'] for operation in key_operations)
                        raise ProtocolError(
                            '%s group contradicts desired state for %s' %
                            (operation_ids, key[1]))
                    continue
                if desired_present != bool(presence_witnesses):
                    operation_ids = ', '.join(
                        operation['id'] for operation in key_operations)
                    raise ProtocolError(
                        '%s group contradicts desired state for %s' %
                        (operation_ids, key[1]))


def _reject_nonfinite(value):
    raise ProtocolError("non-finite JSON number '%s'" % value)


def _validate_record_configuration(record, invocation):
    configuration = invocation.configuration
    if configuration is None:
        return

    expected_selected = {'status': 'known', 'value': configuration}
    metadata = record.get('invocation')
    if metadata.get('configuration') != expected_selected:
        raise ProtocolError(
            'invocation.configuration does not match named database')

    expected_analyzed = {'status': 'known', 'value': [configuration]}
    if record.get('analyzed_configurations') != expected_analyzed:
        raise ProtocolError(
            'analyzed_configurations does not match named database')

    operations = record.get('operations')
    if not isinstance(operations, list):
        raise ProtocolError('record operations are missing')
    for operation in operations:
        if not isinstance(operation, dict):
            raise ProtocolError('record operation is not an object')
        multi_config = operation.get('multi_config')
        if not isinstance(multi_config, dict):
            raise ProtocolError('operation multi_config is missing')
        contributions = multi_config.get('value')
        if (multi_config.get('status') != 'known' or
                not isinstance(contributions, list) or
                len(contributions) != 1 or
                not isinstance(contributions[0], dict) or
                contributions[0].get('configuration') != configuration):
            raise ProtocolError(
                'operation multi_config does not match named database')


def _parse_json_object(line):
    try:
        value = json.loads(
            line, object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_nonfinite)
    except ProtocolError:
        raise
    except (TypeError, ValueError) as why:
        raise ProtocolError('invalid JSON: %s' % why)
    if not isinstance(value, dict):
        raise ProtocolError('JSON Lines record is not an object')
    return value


def _validate_record(record, invocation, previous_path, identities,
                     source_digests=None, source_contents=None):
    if type(record.get('schema_version')) is not int or \
            record['schema_version'] != invocation.output_schema_version:
        raise ProtocolError('record has unsupported or unexpected schema_version')
    if record['schema_version'] == 2:
        _validate_schema_v2(record)
    if record.get('record_type') != 'file_analysis':
        raise ProtocolError('record has invalid record_type')

    metadata = record.get('invocation')
    if not isinstance(metadata, dict):
        raise ProtocolError('record invocation metadata is missing')
    _validate_record_configuration(record, invocation)
    expected = {
        'index': invocation.database_index,
        'id': invocation.invocation_id,
        'command_digest': invocation.command_digest,
        'translation_unit': invocation.translation_unit,
    }
    for key, expected_value in expected.items():
        actual = metadata.get(key)
        if key == 'index' and type(actual) is not int:
            raise ProtocolError('invocation.index is not an integer')
        if actual != expected_value:
            raise ProtocolError('invocation.%s does not match the command' % key)

    digest_hex = metadata['command_digest'][len('sha256:'):]
    derived_id = 'inv-%06d-%s' % (metadata['index'], digest_hex[:12])
    if metadata['id'] != derived_id:
        raise ProtocolError('invocation.id does not match index and digest')

    file_metadata = record.get('file')
    if not isinstance(file_metadata, dict):
        raise ProtocolError('record file metadata is missing')
    path = file_metadata.get('path')
    if not isinstance(path, str) or not path:
        raise ProtocolError('file.path is not a non-empty string')
    if previous_path is not None and path < previous_path:
        raise ProtocolError('records are not in normalized target-file order')

    if record['schema_version'] == 2:
        display_source = path if os.path.isabs(path) else os.path.join(
            invocation.cwd, path)
        expected_identity = os.path.realpath(display_source).replace(
            os.path.sep, '/')
        producer_identity = record['source_identity']
        if (not os.path.isabs(producer_identity) or
                not _normalized_output_path(producer_identity)):
            raise ProtocolError(
                'source_identity is not a normalized absolute path')
        if producer_identity != expected_identity:
            raise ProtocolError(
                'source_identity does not match file.path physical identity')
        try:
            with open(expected_identity, 'rb') as source_file:
                source_bytes = source_file.read()
        except OSError as why:
            raise ProtocolError('cannot verify source_digest for %s: %s' %
                                (path, why))
        current_digest = 'sha256:' + hashlib.sha256(source_bytes).hexdigest()
        producer_digest = record['source_digest']
        if source_digests is not None:
            previous_digest = source_digests.get(producer_identity)
            if (previous_digest is not None and
                    previous_digest != producer_digest):
                raise ProtocolError(
                    'same physical source has different producer-computed bytes')
        if producer_digest != current_digest:
            raise ProtocolError(
                'source_digest does not match current source bytes for %s' % path)
        if source_contents is not None:
            previous_bytes = source_contents.get(producer_identity)
            if previous_bytes is not None and previous_bytes != source_bytes:
                raise ProtocolError(
                    'same physical source changed during JSON validation')
            source_contents[producer_identity] = source_bytes
        if source_digests is not None:
            source_digests[producer_identity] = producer_digest
        _validate_declaration_facts(
            record, source_bytes, schema_validated=True)

    identity = (record['schema_version'], metadata['index'], metadata['id'],
                metadata['translation_unit'], path)
    if identity in identities:
        raise ProtocolError('duplicate record identity')
    identities.add(identity)
    return path


def parse_json_output(output, invocation, identities=None,
                      source_digests=None, source_contents=None):
    """Parse and validate one child's complete LF-framed stdout atomically.

    Return the original JSON records so driver emission does not alter native
    escaping or field order. A stream may have one terminal LF, but every other
    LF must separate two non-empty records; CR is never a record separator.
    """
    if identities is None:
        identities = set()
    if source_digests is None:
        source_digests = {}
    if source_contents is None:
        source_contents = {}
    if output == '':
        return []
    if '\r' in output:
        raise ProtocolError('JSON Lines output contains CR; LF framing required')

    if output.endswith('\n'):
        output = output[:-1]
    if not output:
        raise ProtocolError('JSON Lines output contains an empty record')

    lines = output.split('\n')
    previous_path = None
    for line in lines:
        if not line:
            raise ProtocolError('JSON Lines output contains an empty record')
        if not line.strip():
            raise ProtocolError('JSON Lines output contains a whitespace record')
        record = _parse_json_object(line)
        previous_path = _validate_record(
            record, invocation, previous_path, identities, source_digests,
            source_contents)
    return lines




def _operation_range_key(source_range):
    rank = {'known': 0, 'unknown': 1, 'unavailable': 2,
            'not_applicable': 3}
    status = source_range['status']
    if status != 'known':
        return (rank[status], '', 0, 0, 0, 0)
    start = source_range['value']['start']
    end = source_range['value']['end']
    return (rank[status], start['path'], start['line'], start['column'],
            end['line'], end['column'])


def _operation_condition_key(condition):
    rank = {'known': 0, 'unknown': 1, 'unavailable': 2,
            'not_applicable': 3}
    status = condition['status']
    if status != 'known':
        return (rank[status], '', '', 0, 0, False, '', ())
    value = condition['value']
    location = value['location']
    if location['status'] == 'known':
        location_value = location['value']
        path = location_value['path']
        line = location_value['line']
        column = location_value['column']
    else:
        path, line, column = '', 0, 0
    return (rank[status], value['expression'], path, line, column,
            value['active_branch'], value.get('source_region_id', ''),
            tuple(value.get('source_region_path', ())))


def _operation_sort_key(operation):
    subject_rank = {'remove': 0, 'add': 1, 'keep': 2}
    disposition_rank = {'apply': 0, 'keep': 1, 'suppress': 2, 'report': 3}
    item_rank = {'include': 0, 'forward_declaration': 1}
    return (subject_rank[operation['subject_action']],
            disposition_rank[operation['disposition']],
            item_rank[operation['item_kind']],
            _operation_range_key(operation['source_range']),
            operation['spelling'], operation['reason_code'],
            _operation_condition_key(operation['edit_condition']))

def _aggregate_declaration_conflicts(groups, invocations, source_contents):
    """Aggregate incompatible declarations and suppress only unsafe pairs."""
    parsed_groups = [
        [_parse_json_object(line) for line in lines] for lines in groups]
    by_file = {}
    for invocation, records in zip(invocations, parsed_groups):
        if invocation.configuration is None:
            continue
        for record in records:
            by_file.setdefault(record['source_identity'], []).append(
                (invocation.configuration, record))

    def symbol_names(operation):
        names = set()
        for symbol in operation['symbols']:
            qualified = symbol['qualified_name']
            if qualified['status'] == 'known':
                names.add(qualified['value'])
        return names

    for _, configured_records in by_file.items():
        declarations = {}
        for configuration, record in configured_records:
            for fact in record['declaration_facts']:
                name = fact['qualified_name']
                declarations.setdefault(name, set()).add(
                    (configuration, fact['kind']))

        conflicts = []
        dangerous_names = set()
        source_digest = configured_records[0][1]['source_digest']
        for qualified_name, entries in declarations.items():
            configurations = set(configuration for configuration, _ in entries)
            kinds = set(kind for _, kind in entries)
            if (len(configurations) < 2 or
                    not _declaration_kinds_incompatible(kinds)):
                continue
            dangerous_names.add(qualified_name)
            conflicts.append({
                'qualified_name': qualified_name,
                'source_digest': source_digest,
                'declarations': [
                    {'configuration': configuration, 'kind': kind}
                    for configuration, kind in sorted(entries)],
            })
        conflicts.sort(key=lambda conflict: conflict['qualified_name'])

        for _, record in configured_records:
            record['declaration_conflicts'] = conflicts
            blocked_adds = []
            blocked_names = set()
            for operation in record['operations']:
                if (operation['subject_action'] == 'add' and
                        operation['disposition'] == 'apply' and
                        operation['item_kind'] == 'forward_declaration'):
                    names = symbol_names(operation) & dangerous_names
                    if names:
                        blocked_adds.append(operation)
                        blocked_names.update(names)
            if not blocked_adds:
                _validate_declaration_facts(
                    record, source_contents[record['source_identity']],
                    native_conflicts=False)
                continue

            paired_removals = []
            for operation in record['operations']:
                if (operation['subject_action'] != 'remove' or
                        operation['disposition'] != 'apply' or
                        operation['item_kind'] != 'include'):
                    continue
                if symbol_names(operation) & blocked_names:
                    paired_removals.append(operation)

            blocked_operations = blocked_adds + paired_removals
            for operation in blocked_operations:
                operation['disposition'] = 'report'
                operation['reason_code'] = 'declaration_kind_conflict'
                operation['reason'] = (
                    'Cross-configuration declaration kinds make this '
                    'forward-declaration replacement unsafe.')
                if operation['policy']['status'] == 'known':
                    operation['policy'] = {
                        'status': 'unavailable', 'value': None}
                multi_config = operation['multi_config']
                if multi_config['status'] == 'known':
                    for contribution in multi_config['value']:
                        if contribution['disposition'] == 'apply':
                            contribution['disposition'] = 'report'

            desired = record['desired_state']
            if desired['status'] != 'known' or not desired['complete']:
                raise ProtocolError(
                    'unsafe replacement lacks a complete desired state')
            blocked_add_keys = set(
                (operation['item_kind'], operation['spelling'])
                for operation in blocked_adds)
            desired['items'] = [
                item for item in desired['items']
                if (item['item_kind'], item['spelling']) not in blocked_add_keys]
            desired_include_tokens = set(
                _logical_include_token(item['spelling'])
                for item in desired['items']
                if item['item_kind'] == 'include')
            restored_includes = []
            for operation in paired_removals:
                include_token = _logical_include_token(operation['spelling'])
                if include_token not in desired_include_tokens:
                    restored_includes.append({
                        'item_kind': 'include',
                        'spelling': _logical_include_spelling(operation),
                        'symbols': sorted(symbol_names(operation)),
                    })
                    desired_include_tokens.add(include_token)
            if restored_includes:
                first_forward = next(
                    (index for index, item in enumerate(desired['items'])
                     if item['item_kind'] == 'forward_declaration'),
                    len(desired['items']))
                desired['items'][first_forward:first_forward] = \
                    restored_includes

            record['operations'].sort(key=_operation_sort_key)
            for index, operation in enumerate(record['operations'], 1):
                operation['id'] = 'op-%d' % index
            has_applied_edit = any(
                operation['disposition'] == 'apply' and
                operation['subject_action'] in ('add', 'remove')
                for operation in record['operations'])
            record['status'] = 'changes' if has_applied_edit else 'correct'
            record['diagnostics'].append({
                'severity': 'warning',
                'code': 'declaration_kind_conflict',
                'message': (
                    'An unsafe forward-declaration replacement was reported; '
                    'unrelated safe edits remain applicable.'),
                'location': {'status': 'unavailable', 'value': None},
            })
            _validate_declaration_facts(
                record, source_contents[record['source_identity']],
                native_conflicts=False)

    return [[_json_dumps_canonical(record) for record in records]
            for records in parsed_groups]

def _protocol_error_context(invocation):
    if invocation.database_name is None:
        return 'database index %d' % invocation.database_index
    return "database '%s' entry index %d (invocation index %d)" % (
        invocation.database_name, invocation.database_local_index,
        invocation.database_index)


def _collect_json_processes(invocations, verbose, jobs, max_load_average=0):
    """Run invocations and return database-ordered buffered child results."""
    _validate_jobs(jobs)
    ordered = sorted(invocations, key=lambda invocation:
                     invocation.database_index)
    results = [None] * len(ordered)
    pending = []
    next_invocation = 0

    while next_invocation < len(ordered) or pending:
        complete = [item for item in pending if item[1].poll() is not None]
        for position, process in complete:
            pending.remove((position, process))
            try:
                stdout = process.get_stdout()
                stdout_error = None
            except UnicodeError as why:
                stdout = ''
                stdout_error = why
            try:
                stderr = process.get_stderr()
            except UnicodeError as why:
                stderr = ''
                if stdout_error is None:
                    stdout_error = why
            results[position] = (stdout, stderr, process.returncode,
                                 stdout_error)

        capacity = jobs - len(pending)
        if max_load_average > 0 and capacity > 0:
            one_min_load_average, _, _ = os.getloadavg()
            load_capacity = max_load_average - one_min_load_average
            capacity = min(capacity, max(0, int(load_capacity)))
            if not capacity and not pending:
                capacity = 1

        end = min(len(ordered), next_invocation + capacity)
        for position in range(next_invocation, end):
            pending.append((position, ordered[position].start(verbose)))
        next_invocation = end
        time.sleep(0.0001)
    return ordered, results


def _validate_json_processes(ordered, results, identities=None,
                             source_digests=None, source_contents=None):
    identities = set() if identities is None else identities
    source_digests = {} if source_digests is None else source_digests
    source_contents = {} if source_contents is None else source_contents
    groups = []
    errors = []
    exit_code = 0
    for invocation, result in zip(ordered, results):
        stdout, stderr, returncode, decode_error = result
        child_exit = returncode if returncode >= 0 else 1
        exit_code = max(exit_code, child_exit)
        if decode_error is not None:
            errors.append((invocation,
                           'output is not valid UTF-8: %s' % decode_error))
            groups.append([])
            continue
        try:
            lines = parse_json_output(
                stdout, invocation, identities, source_digests,
                source_contents)
            if returncode == 0 and not lines:
                raise ProtocolError('successful child produced no records')
            groups.append(lines)
        except ProtocolError as why:
            errors.append((invocation, str(why)))
            groups.append([])
    return groups, errors, exit_code


def execute_json(invocations, verbose, jobs, max_load_average=0):
    """Run structured invocations and atomically emit validated JSON groups."""
    ordered, results = _collect_json_processes(
        invocations, verbose, jobs, max_load_average)


    # Validate all groups before writing any structured stdout.
    identities = set()
    source_digests = {}
    source_contents = {}
    groups, errors, exit_code = _validate_json_processes(
        ordered, results, identities, source_digests, source_contents)

    # Native diagnostics use stderr exclusively and remain grouped by DB index.
    for _, result in zip(ordered, results):
        stderr = result[1]
        if stderr:
            sys.stderr.write(stderr)
    for invocation, message in errors:
        print('error: IWYU JSON protocol at %s: %s' %
              (_protocol_error_context(invocation), message), file=sys.stderr)

    if errors:
        return max(exit_code, 1)
    if groups and all(invocation.output_schema_version == 2
                      for invocation in ordered):
        try:
            groups = _aggregate_declaration_conflicts(
                groups, ordered, source_contents)
        except ProtocolError as why:
            print('error: IWYU JSON aggregation: %s' % why, file=sys.stderr)
            return max(exit_code, 1)
    for lines in groups:
        for line in lines:
            sys.stdout.write(line + '\n')
    return exit_code

def _emit_buffered_json_stderr(buffered, errors):
    for _, text in sorted(buffered, key=lambda item: item[0]):
        if text:
            sys.stderr.write(text)
    for invocation, message in sorted(
            errors, key=lambda item: item[0].database_index):
        print('error: IWYU JSON protocol at %s: %s' %
              (_protocol_error_context(invocation), message), file=sys.stderr)


def execute_header_analysis(database_plans, real_invocations, headers,
                            extra_args, verbose, jobs, max_load_average,
                            max_synthetic_invocations):
    """Run DRIVER-001's atomic real-then-uncovered-header analysis."""
    identities = set()
    source_digests = {}
    source_contents = {}
    all_pairs = []
    buffered_stderr = []
    all_errors = []

    # Bound the complete prospective product before phase 1. Coverage is known
    # only after real children run, so checking merely the uncovered remainder
    # would allow a selected database to start children before failing its cap.
    prospective_count = 0
    for plan in database_plans:
        prospective_count += len(headers) * len(plan['profiles'])
        if prospective_count > max_synthetic_invocations:
            print('error: prospective synthetic header invocation count %d '
                  'exceeds cap %d' %
                  (prospective_count, max_synthetic_invocations),
                  file=sys.stderr)
            return 1

    # Selection and every phase boundary revalidate both bytes and filesystem
    # identity before any buffered native output can become public.
    try:
        _verify_header_snapshots(headers)
    except (IOError, ValueError) as why:
        print('error: %s' % why, file=sys.stderr)
        return 1

    ordered, results = _collect_json_processes(
        real_invocations, verbose, jobs, max_load_average)
    groups, errors, exit_code = _validate_json_processes(
        ordered, results, identities, source_digests, source_contents)
    buffered_stderr.extend(
        (invocation.database_index, result[1])
        for invocation, result in zip(ordered, results))
    all_errors.extend(errors)
    all_pairs.extend(zip(ordered, groups))

    try:
        _verify_header_snapshots(headers)
        _verify_source_contents(source_contents)
    except (IOError, ValueError) as why:
        _emit_buffered_json_stderr(buffered_stderr, all_errors)
        print('error: %s' % why, file=sys.stderr)
        return 1
    if errors or exit_code:
        _emit_buffered_json_stderr(buffered_stderr, all_errors)
        return max(exit_code, 1)

    selected_identities = {
        normcase(path.replace(os.path.sep, '/')): relative
        for relative, path, _, _, _ in headers
    }
    covered = set()
    for invocation, lines in all_pairs:
        for line in lines:
            record = _parse_json_object(line)
            identity = record.get('source_identity')
            selected_relative = selected_identities.get(normcase(identity or ''))
            if selected_relative is None:
                continue
            status = record.get('status')
            if status not in ('correct', 'changes'):
                all_errors.append((
                    invocation,
                    'selected header %s has non-terminal real status %r' %
                    (selected_relative, status)))
                continue
            covered.add((invocation.configuration, normcase(identity)))
    if all_errors:
        _emit_buffered_json_stderr(buffered_stderr, all_errors)
        return 1

    synthetic = []
    expected = {}
    for plan in database_plans:
        configuration = plan['configuration']
        for header_index, (relative, path, digest, _, _) in enumerate(headers):
            if (configuration, normcase(path.replace(os.path.sep, '/'))) in covered:
                continue
            for profile_index, profile in enumerate(plan['profiles']):
                local_index = (len(plan['compilation_db']) +
                               header_index * len(plan['profiles']) +
                               profile_index)
                database_index = plan['base'] + local_index
                entry = instantiate_synthetic_entry(profile, path)
                invocation = Invocation.from_compile_command(
                    entry, [], database_index, JSON_FORMAT,
                    configuration=configuration,
                    database_name=plan['database_name'],
                    database_local_index=local_index,
                    output_schema_version=2)
                synthetic.append(invocation)
                expected[database_index] = (path.replace(os.path.sep, '/'),
                                            digest, relative, profile_index)

    if synthetic:
        ordered2, results2 = _collect_json_processes(
            synthetic, verbose, jobs, max_load_average)
        groups2, errors2, exit_code2 = _validate_json_processes(
            ordered2, results2, identities, source_digests, source_contents)
        buffered_stderr.extend(
            (invocation.database_index, result[1])
            for invocation, result in zip(ordered2, results2))
        all_errors.extend(errors2)
        exit_code = max(exit_code, exit_code2)
        filtered_groups = []
        for invocation, lines in zip(ordered2, groups2):
            target, digest, relative, _ = expected[invocation.database_index]
            targets = []
            for line in lines:
                record = _parse_json_object(line)
                if record.get('source_identity') == target:
                    targets.append((line, record))
            if len(targets) != 1:
                all_errors.append((
                    invocation,
                    'synthetic invocation for %s produced %d target records' %
                    (relative, len(targets))))
                filtered_groups.append([])
                continue
            line, record = targets[0]
            if record.get('source_digest') != digest:
                all_errors.append((
                    invocation,
                    'synthetic target source digest differs from snapshot'))
                filtered_groups.append([])
                continue
            if record.get('status') not in ('correct', 'changes'):
                all_errors.append((
                    invocation,
                    'synthetic target has non-terminal status %r' %
                    record.get('status')))
                filtered_groups.append([])
                continue
            filtered_groups.append([line])
        all_pairs.extend(zip(ordered2, filtered_groups))

    try:
        _verify_header_snapshots(headers)
        _verify_source_contents(source_contents)
    except (IOError, ValueError) as why:
        _emit_buffered_json_stderr(buffered_stderr, all_errors)
        print('error: %s' % why, file=sys.stderr)
        return 1

    _emit_buffered_json_stderr(buffered_stderr, all_errors)
    if all_errors or exit_code:
        return max(exit_code, 1)

    all_pairs.sort(key=lambda item: item[0].database_index)
    final_invocations = [item[0] for item in all_pairs]
    final_groups = [item[1] for item in all_pairs]
    try:
        final_groups = _aggregate_declaration_conflicts(
            final_groups, final_invocations, source_contents)
        _verify_header_snapshots(headers)
        _verify_source_contents(source_contents)
    except (IOError, ValueError, ProtocolError) as why:
        print('error: IWYU JSON aggregation/final verification: %s' % why,
              file=sys.stderr)
        return 1
    for lines in final_groups:
        for line in lines:
            sys.stdout.write(line + '\n')
    return 0




def _deduplicate_selected_entries(compilation_db):
    selected_ids = set()
    unique_entries = []
    for entry in compilation_db:
        if id(entry) in selected_ids:
            continue
        selected_ids.add(id(entry))
        unique_entries.append(entry)
    return unique_entries


def main(compilation_db_path, source_files, verbose, formatter, jobs,
         max_load_average, extra_args, output_format=DEFAULT_FORMAT,
         named_databases=None, output_schema_version=1,
         configuration=None, analyze_header_root=None,
         analyze_header_globs=None, analyze_header_manifest=None,
         max_synthetic_header_invocations=None):
    """Run IWYU for one legacy database or ordered named databases."""

    try:
        _validate_jobs(jobs)
    except (TypeError, ValueError) as why:
        print('error: %s' % why, file=sys.stderr)
        return 1

    if not IWYU_EXECUTABLE:
        print('error: include-what-you-use executable not found',
              file=sys.stderr)
        return 1

    if output_format != JSON_FORMAT and output_schema_version != 1:
        print('error: --output-schema requires JSON output', file=sys.stderr)
        return 1

    named_databases = list(named_databases or [])
    header_feature = bool(analyze_header_root or analyze_header_globs or
                          analyze_header_manifest or
                          max_synthetic_header_invocations is not None)
    headers = None
    if header_feature:
        if output_format != JSON_FORMAT or output_schema_version != 2:
            print('error: header analysis requires JSON output schema 2',
                  file=sys.stderr)
            return 1
        if not analyze_header_root:
            print('error: header analysis requires --analyze-header-root',
                  file=sys.stderr)
            return 1
        if named_databases and configuration is not None:
            print('error: --configuration is only valid with single -p mode',
                  file=sys.stderr)
            return 1
        if not named_databases:
            try:
                validate_configuration_name(configuration)
            except ValueError:
                print('error: single -p header analysis requires an exact '
                      '--configuration', file=sys.stderr)
                return 1
        if max_synthetic_header_invocations is None:
            max_synthetic_header_invocations = 1000
        try:
            _validate_jobs(max_synthetic_header_invocations)
            _, headers = select_headers(
                analyze_header_root, analyze_header_globs,
                analyze_header_manifest)
        except (IOError, TypeError, ValueError) as why:
            print('error: %s' % why, file=sys.stderr)
            return 1
    elif configuration is not None:
        print('error: --configuration requires header analysis', file=sys.stderr)
        return 1

    try:
        if named_databases:
            names = set()
            paths = {}
            requested = []
            for name, path in named_databases:
                validate_configuration_name(name)
                if name in names:
                    raise ValueError("duplicate named database '%s'" % name)
                names.add(name)
                canonical_path = canonical_compilation_db_path(path)
                path_key = normcase(canonical_path)
                if path_key in paths:
                    raise ValueError(
                        "named databases '%s' and '%s' alias path '%s'" %
                        (paths[path_key], name, canonical_path))
                paths[path_key] = name
                requested.append((name, canonical_path))

            databases = []
            for name, path in requested:
                canonical_path, compilation_db = load_compilation_db(
                    path, require_nonempty=True)
                databases.append((name, canonical_path, compilation_db))
        else:
            canonical_path, compilation_db = load_compilation_db(
                compilation_db_path)
            databases = [(configuration if header_feature else None,
                          canonical_path, compilation_db)]
    except (TypeError, ValueError) as why:
        print('error: %s' % why, file=sys.stderr)
        return 1

    invocations = []
    plans = []
    invocation_base = 0
    try:
        for name, canonical_path, compilation_db in databases:
            original_indices = {
                id(entry): index for index, entry in enumerate(compilation_db)}
            selected = slice_compilation_db(compilation_db, source_files)
            if output_format == JSON_FORMAT:
                selected = _deduplicate_selected_entries(selected)
            if header_feature and not selected:
                raise ValueError(
                    "configuration '%s' has no selected compilation entries" %
                    name)

            profiles = []
            if header_feature:
                profile_keys = set()
                for entry in selected:
                    profile_entry = _entry_with_driver_arguments(
                        entry, extra_args)
                    profile = synthetic_compile_profile(profile_entry)
                    if profile['key'] not in profile_keys:
                        profile_keys.add(profile['key'])
                        profiles.append(profile)
                if not profiles:
                    raise ValueError(
                        "configuration '%s' has no synthetic compile profiles" %
                        name)

            bound_driver_args = list(extra_args)
            if header_feature:
                for _, path, _, _, _ in headers:
                    bound_driver_args.extend(_exact_check_also(path))

            for entry in selected:
                local_index = original_indices[id(entry)]
                if header_feature:
                    bound_entry = _entry_with_driver_arguments(
                        entry, bound_driver_args)
                    safe_profile = synthetic_compile_profile(bound_entry)
                    analysis_entry = instantiate_synthetic_entry(
                        safe_profile, os.path.realpath(entry['file']))
                else:
                    analysis_entry = entry
                invocations.append(Invocation.from_compile_command(
                    analysis_entry, [] if header_feature else extra_args,
                    invocation_base + local_index,
                    output_format, configuration=name, database_name=(
                        name if named_databases else None),
                    database_local_index=local_index,
                    output_schema_version=output_schema_version))

            if header_feature:
                plans.append({
                    'configuration': name,
                    'database_name': name if named_databases else None,
                    'path': canonical_path,
                    'compilation_db': compilation_db,
                    'profiles': profiles,
                    'base': invocation_base,
                })
                invocation_base += (len(compilation_db) +
                                    len(headers) * len(profiles))
            else:
                invocation_base += len(compilation_db)
    except (KeyError, TypeError, ValueError) as why:
        print('error: failed to prepare IWYU invocation: %s' % why,
              file=sys.stderr)
        return 1

    if header_feature:
        return execute_header_analysis(
            plans, invocations, headers, extra_args, verbose, jobs,
            max_load_average, max_synthetic_header_invocations)
    if output_format == JSON_FORMAT:
        return execute_json(invocations, verbose, jobs, max_load_average)
    return execute(invocations, verbose, formatter, jobs, max_load_average)


def _bootstrap(sys_argv):
    """ Parse arguments and dispatch to main(). """

    # This hackery is necessary to add the forwarded IWYU args to the
    # usage and help strings.
    def customize_usage(parser):
        """ Rewrite the parser's format_usage. """
        original_format_usage = parser.format_usage
        parser.format_usage = lambda: original_format_usage().rstrip() + \
                              ' -- [<IWYU args>]' + os.linesep

    def customize_help(parser):
        """ Rewrite the parser's format_help. """
        original_format_help = parser.format_help

        def custom_help():
            """ Customized help string, calls the adjusted format_usage. """
            helplines = original_format_help().splitlines()
            while helplines and helplines[0].strip():
                del helplines[0]
            return (parser.format_usage().rstrip() + os.linesep +
                    os.linesep.join(helplines) + os.linesep)

        parser.format_help = custom_help

    # Parse arguments.
    parser = argparse.ArgumentParser(
        description='Include-what-you-use compilation database driver.',
        epilog='Assumes include-what-you-use is available on the PATH.')
    customize_usage(parser)
    customize_help(parser)

    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Print IWYU commands')
    parser.add_argument('-o', '--output-format', type=str,
                        choices=list(FORMATTERS.keys()) + [JSON_FORMAT],
                        default=DEFAULT_FORMAT,
                        help='Output format (default: %s)' % DEFAULT_FORMAT)
    parser.add_argument('--output-schema', type=int, choices=[1, 2], default=1,
                        help=('Structured JSON schema version; version 2 adds '
                              'declaration conflict aggregation'))
    parser.add_argument('-j', '--jobs', type=_positive_jobs, default=1,
                        help='Number of concurrent subprocesses')
    parser.add_argument('-l', '--load', type=float, default=0,
                        help='Do not start new jobs if the 1min load average is greater than the provided value')
    parser.add_argument(
        '--configuration', type=validate_configuration_name,
        help=('Configuration name for single -p header analysis; named '
              'database names are their configurations'))
    parser.add_argument(
        '--analyze-header-root', metavar='<absolute-directory>',
        help='Canonical physical root containing headers selected for analysis')
    parser.add_argument(
        '--analyze-header-glob', action='append', dest='analyze_header_globs',
        metavar='<root-relative-glob>',
        help=('Select rooted headers (repeatable; mutually exclusive with '
              '--analyze-header-manifest)'))
    parser.add_argument(
        '--analyze-header-manifest', metavar='<absolute-manifest>',
        help='Canonical JSON manifest of rooted header paths and SHA-256 digests')
    parser.add_argument(
        '--max-synthetic-header-invocations', type=_positive_jobs,
        metavar='<count>',
        help='Fail atomically if uncovered-header invocations exceed this cap')
    database_group = parser.add_mutually_exclusive_group(required=True)
    database_group.add_argument(
        '-p', metavar='<build-path>',
        help='Legacy single compilation database path', dest='dbpath')
    database_group.add_argument(
        '--database', action='append', type=parse_database_spec,
        metavar='<name>=<build-path>', dest='databases',
        help=('Named compilation database (repeat in analysis order; JSON '
              'mode binds NAME to --configuration)'))
    parser.add_argument('source', nargs='*',
                        help=('Zero or more source files (or directories) to '
                              'run IWYU on. Defaults to all in compilation '
                              'database.'))

    def partition_args(argv):
        """ Split around '--' into driver args and IWYU args. """
        try:
            double_dash = argv.index('--')
            return argv[:double_dash], argv[double_dash+1:]
        except ValueError:
            return argv, []
    argv, extra_args = partition_args(sys_argv[1:])
    args = parser.parse_args(argv)

    return main(args.dbpath, args.source, args.verbose,
                FORMATTERS.get(args.output_format), args.jobs, args.load,
                extra_args, args.output_format,
                named_databases=args.databases,
                output_schema_version=args.output_schema,
                configuration=args.configuration,
                analyze_header_root=args.analyze_header_root,
                analyze_header_globs=args.analyze_header_globs,
                analyze_header_manifest=args.analyze_header_manifest,
                max_synthetic_header_invocations=(
                    args.max_synthetic_header_invocations))


if __name__ == '__main__':
    sys.exit(_bootstrap(sys.argv))
