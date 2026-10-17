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
            # arguments is a command-line in list form.
            command = list(entry['arguments'])
        elif 'command' in entry:
            # command is a command-line in string form, split to list.
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
        canonical_args = expand_response_files(
            command, real_directory, windows=windows_args)
        canonical_args = _unwrap_compiler_wrapper(canonical_args)
        if not canonical_args:
            raise ValueError('expanded compilation command contains only a wrapper')

        # The compiler itself can be hidden in a response file. GNU response
        # tokenization may be good enough to reveal clang-cl, but it consumes
        # literal backslashes in that file and every nested response file.
        # Start over from the unexpanded command so identity and launch use
        # clang-cl's Windows tokenization at every level.
        if not windows_args and is_msvc_driver(canonical_args[0]):
            windows_args = True
            canonical_args = expand_response_files(
                command, real_directory, windows=True)
            canonical_args = _unwrap_compiler_wrapper(canonical_args)
            if not canonical_args:
                raise ValueError(
                    'expanded compilation command contains only a wrapper')

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
        # An include removal edits the present include occurrence itself. Its
        # conditional anchor must therefore remain in active file scope. Add
        # and keep conditions describe provenance, not an insertion/removal
        # point, and may honestly originate in a function, class, namespace,
        # or declaration scope. Consumers decide whether such provenance is an
        # safe insertion site.
        require_file_scope = (
            operation['subject_action'] == 'remove' and
            operation['item_kind'] == 'include')
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
    identities = [
        (operation['subject_action'], operation['item_kind'],
         operation['spelling'],
         _json_dumps_canonical(operation['source_range']))
        for operation in operations]
    if len(identities) != len(set(identities)):
        raise ProtocolError('duplicate semantic operation identity')

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
            include = re.match(r'^#include\s+(<[^>]+>|"[^"]+")$',
                               operation['spelling'])
            if (not include or
                    include.group(1) != selected['value']['spelling']):
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
        desired_keys = [(item['item_kind'], item['spelling'])
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


def _operation_sort_key(operation):
    subject_rank = {'remove': 0, 'add': 1, 'keep': 2}
    disposition_rank = {'apply': 0, 'keep': 1, 'suppress': 2, 'report': 3}
    item_rank = {'include': 0, 'forward_declaration': 1}
    return (subject_rank[operation['subject_action']],
            disposition_rank[operation['disposition']],
            item_rank[operation['item_kind']],
            _operation_range_key(operation['source_range']),
            operation['spelling'], operation['reason_code'])

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
            desired_keys = set((item['item_kind'], item['spelling'])
                               for item in desired['items'])
            restored_includes = []
            for operation in paired_removals:
                key = (operation['item_kind'], operation['spelling'])
                if key not in desired_keys:
                    restored_includes.append({
                        'item_kind': operation['item_kind'],
                        'spelling': operation['spelling'],
                        'symbols': sorted(symbol_names(operation)),
                    })
                    desired_keys.add(key)
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


def execute_json(invocations, verbose, jobs, max_load_average=0):
    """Run structured invocations and atomically emit validated JSON groups."""
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

    # Validate all groups before writing any structured stdout.
    identities = set()
    source_digests = {}
    source_contents = {}
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
         named_databases=None, output_schema_version=1):
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
            invocation_base = 0
            for name, path in requested:
                canonical_path, compilation_db = load_compilation_db(
                    path, require_nonempty=True)
                databases.append(
                    (name, canonical_path, compilation_db, invocation_base))
                invocation_base += len(compilation_db)
        else:
            canonical_path, compilation_db = load_compilation_db(
                compilation_db_path)
            databases = [(None, canonical_path, compilation_db, 0)]
    except (TypeError, ValueError) as why:
        print('error: %s' % why, file=sys.stderr)
        return 1

    invocations = []
    try:
        for name, _, compilation_db, invocation_base in databases:
            original_indices = {
                id(entry): index for index, entry in enumerate(compilation_db)}
            selected = slice_compilation_db(compilation_db, source_files)
            if output_format == JSON_FORMAT:
                selected = _deduplicate_selected_entries(selected)

            # Index named invocations in database order, retaining each entry's
            # original local position and gaps left by source selection.
            for entry in selected:
                local_index = original_indices[id(entry)]
                invocations.append(Invocation.from_compile_command(
                    entry, extra_args, invocation_base + local_index,
                    output_format, configuration=name, database_name=name,
                    database_local_index=local_index,
                    output_schema_version=output_schema_version))
    except (KeyError, TypeError, ValueError) as why:
        print('error: failed to prepare IWYU invocation: %s' % why,
              file=sys.stderr)
        return 1

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
                output_schema_version=args.output_schema)


if __name__ == '__main__':
    sys.exit(_bootstrap(sys.argv))
