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

See iwyu_tool.py -h for more details on command-line arguments.
"""
from __future__ import print_function
import os
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
    '--invocation_index',
    '--invocation_id',
    '--command_digest',
    '--translation_unit',
])
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
    '--invocation_index',
    '--invocation_id',
    '--command_digest',
    '--translation_unit',
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


def reject_reserved_json_args(arguments):
    """Reject effective -Xiwyu pairs owned by the driver JSON protocol."""
    for index, argument in enumerate(arguments[:-1]):
        if argument != '-Xiwyu':
            continue
        iwyu_argument = arguments[index + 1]
        if _resolve_iwyu_long_option(iwyu_argument) in RESERVED_JSON_OPTIONS:
            raise ValueError("reserved IWYU driver argument: '%s'" %
                             iwyu_argument)


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
                 translation_unit=None, structured_output=False):
        self.command = command
        self.cwd = cwd
        self.database_index = database_index
        self.command_digest = command_digest
        self.invocation_id = invocation_id
        self.translation_unit = translation_unit
        self.structured_output = structured_output

    def __str__(self):
        return ' '.join(self.command)

    @classmethod
    def from_compile_command(cls, entry, extra_args, database_index=None,
                             output_format=DEFAULT_FORMAT):
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
        compile_command = command[0]
        windows_args = is_msvc_driver(compile_command)
        extra_args = list(extra_args)

        if not structured_output:
            if windows_args:
                # If the compiler is cl-compatible, let IWYU be cl-compatible.
                extra_args = ['--driver-mode=cl'] + extra_args
            return cls([IWYU_EXECUTABLE] + extra_args + command[1:],
                       entry['directory'])

        reject_reserved_json_args(extra_args)

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

        reject_reserved_json_args(canonical_args[1:])
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
        child_command = ([IWYU_EXECUTABLE] + extra_args + protocol_args +
                         canonical_args[1:])
        return cls(child_command, entry['directory'], database_index, digest,
                   invocation_id, translation_unit, structured_output=True)

    def start(self, verbose):
        """ Run invocation and collect output. """
        if verbose:
            print('# %s' % self, file=sys.stderr)

        return Process.start(self)


def fixup_compilation_db(compilation_db):
    """ Canonicalize paths in JSON compilation database. """
    for entry in compilation_db:
        # Convert relative paths to absolute ones if possible, based on the entry's directory.
        if 'directory' in entry and not os.path.isabs(entry['file']):
            entry['file'] = os.path.join(entry['directory'], entry['file'])

        # Expand relative paths and symlinks
        entry['file'] = os.path.realpath(entry['file'])

    return compilation_db


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


def _reject_nonfinite(value):
    raise ProtocolError("non-finite JSON number '%s'" % value)


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


def _validate_record(record, invocation, previous_path, identities):
    if type(record.get('schema_version')) is not int or \
            record['schema_version'] != 1:
        raise ProtocolError('record has unsupported schema_version')
    if record.get('record_type') != 'file_analysis':
        raise ProtocolError('record has invalid record_type')

    metadata = record.get('invocation')
    if not isinstance(metadata, dict):
        raise ProtocolError('record invocation metadata is missing')
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

    identity = (record['schema_version'], metadata['index'], metadata['id'],
                metadata['translation_unit'], path)
    if identity in identities:
        raise ProtocolError('duplicate record identity')
    identities.add(identity)
    return path


def parse_json_output(output, invocation, identities=None):
    """Parse and validate one child's complete LF-framed stdout atomically.

    Return the original JSON records so driver emission does not alter native
    escaping or field order. A stream may have one terminal LF, but every other
    LF must separate two non-empty records; CR is never a record separator.
    """
    if identities is None:
        identities = set()
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
            record, invocation, previous_path, identities)
    return lines


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
    groups = []
    errors = []
    exit_code = 0
    for invocation, result in zip(ordered, results):
        stdout, stderr, returncode, decode_error = result
        child_exit = returncode if returncode >= 0 else 1
        exit_code = max(exit_code, child_exit)
        if decode_error is not None:
            errors.append((invocation.database_index,
                           'output is not valid UTF-8: %s' % decode_error))
            groups.append([])
            continue
        try:
            lines = parse_json_output(stdout, invocation, identities)
            if returncode == 0 and not lines:
                raise ProtocolError('successful child produced no records')
            groups.append(lines)
        except ProtocolError as why:
            errors.append((invocation.database_index, str(why)))
            groups.append([])

    # Native diagnostics use stderr exclusively and remain grouped by DB index.
    for _, result in zip(ordered, results):
        stderr = result[1]
        if stderr:
            sys.stderr.write(stderr)
    for index, message in errors:
        print('error: IWYU JSON protocol at database index %d: %s' %
              (index, message), file=sys.stderr)

    if errors:
        return max(exit_code, 1)
    for lines in groups:
        for line in lines:
            sys.stdout.write(line + '\n')
    return exit_code


def main(compilation_db_path, source_files, verbose, formatter, jobs,
         max_load_average, extra_args, output_format=DEFAULT_FORMAT):
    """ Entry point. """

    try:
        _validate_jobs(jobs)
    except (TypeError, ValueError) as why:
        print('error: %s' % why, file=sys.stderr)
        return 1

    if not IWYU_EXECUTABLE:
        print('error: include-what-you-use executable not found',
              file=sys.stderr)
        return 1

    try:
        if os.path.isdir(compilation_db_path):
            compilation_db_path = os.path.join(compilation_db_path,
                                               'compile_commands.json')

        # Read compilation db from disk.
        compilation_db_path = os.path.realpath(compilation_db_path)
        with open(compilation_db_path, 'r') as fileobj:
            compilation_db = json.load(fileobj)
        if not isinstance(compilation_db, list):
            raise ValueError('compilation database root is not an array')
    except (IOError, ValueError) as why:
        print('error: failed to parse compilation database: %s' % why,
              file=sys.stderr)
        return 1

    compilation_db = fixup_compilation_db(compilation_db)
    original_indices = {id(entry): index
                        for index, entry in enumerate(compilation_db)}
    compilation_db = slice_compilation_db(compilation_db, source_files)
    if output_format == JSON_FORMAT:
        selected_ids = set()
        unique_entries = []
        for entry in compilation_db:
            if id(entry) in selected_ids:
                continue
            selected_ids.add(id(entry))
            unique_entries.append(entry)
        compilation_db = unique_entries

    try:
        # Keep each entry's position in the original, unsliced database. This
        # deliberately preserves gaps and distinct indices for equal commands.
        invocations = [
            Invocation.from_compile_command(
                entry, extra_args, original_indices[id(entry)], output_format)
            for entry in compilation_db
        ]
    except ValueError as why:
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
            helpmsg = original_format_help()
            helplines = helpmsg.splitlines()
            helplines[0] = parser.format_usage().rstrip()
            return os.linesep.join(helplines) + os.linesep

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
    parser.add_argument('-j', '--jobs', type=_positive_jobs, default=1,
                        help='Number of concurrent subprocesses')
    parser.add_argument('-l', '--load', type=float, default=0,
                        help='Do not start new jobs if the 1min load average is greater than the provided value')
    parser.add_argument('-p', metavar='<build-path>', required=True,
                        help='Compilation database path', dest='dbpath')
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
                extra_args, args.output_format)


if __name__ == '__main__':
    sys.exit(_bootstrap(sys.argv))
