#!/usr/bin/env python3

##===--- iwyu_tool_test.py - test for iwyu_tool.py ------------------------===##
#
#                     The LLVM Compiler Infrastructure
#
# This file is distributed under the University of Illinois Open Source
# License. See LICENSE.TXT for details.
#
##===----------------------------------------------------------------------===##
import os
import sys
import json
import time
import random
import inspect
import tempfile
import unittest
import iwyu_tool

try:
    from cStringIO import StringIO
except ImportError:
    from io import StringIO


class MockProcess(object):
    def __init__(self, block, content, returncode, stderr=''):
        self.content = content
        self.stderr = stderr
        self.complete_ts = time.time() + block
        self.returncode = returncode

    def poll(self):
        if time.time() < self.complete_ts:
            return None
        return 0

    def get_output(self):
        remaining = self.complete_ts - time.time()
        if remaining > 0:
            time.sleep(remaining)
        return self.content

    def get_stdout(self):
        return self.get_output()

    def get_stderr(self):
        remaining = self.complete_ts - time.time()
        if remaining > 0:
            time.sleep(remaining)
        return self.stderr


class MockInvocation(iwyu_tool.Invocation):
    def __init__(self, command=None, cwd=''):
        iwyu_tool.Invocation.__init__(self, command or [], cwd)
        self._will_return = ''
        self._will_block = 0
        self._will_returncode = 0

    def will_block(self, seconds):
        self._will_block = seconds

    def will_return(self, content):
        self._will_return = content

    def will_returncode(self, returncode):
        self._will_returncode = returncode

    def start(self, verbose):
        return MockProcess(self._will_block, self._will_return,
                           self._will_returncode)


class MockJsonInvocation(iwyu_tool.Invocation):
    def __init__(self, database_index, block=0, stdout='', stderr='',
                 returncode=0):
        digest_hex = ('%064x' % (database_index + 1))[-64:]
        digest = 'sha256:' + digest_hex
        invocation_id = 'inv-%06d-%s' % (database_index, digest_hex[:12])
        translation_unit = '/src/tu%d.cc' % database_index
        iwyu_tool.Invocation.__init__(
            self, ['iwyu'], '/build', database_index, digest, invocation_id,
            translation_unit, structured_output=True)
        self._process = MockProcess(block, stdout, returncode, stderr)

    def start(self, verbose):
        return self._process

    def record(self, path=None, status='correct'):
        record = {
            'schema_version': 1,
            'record_type': 'file_analysis',
            'invocation': {
                'id': self.invocation_id,
                'index': self.database_index,
                'translation_unit': self.translation_unit,
                'command_digest': self.command_digest,
            },
            'file': {'path': path or self.translation_unit},
            'status': status,
        }
        return json.dumps(record, separators=(',', ':'))


class MockIwyuToolMain(object):
    """ Replacement for iwyu_tool.main to capture parsed arguments. """
    def __init__(self):
        if hasattr(inspect, 'getfullargspec'):
            getargspec = inspect.getfullargspec
        else:
            getargspec = inspect.getargspec
        self.argspec = getargspec(iwyu_tool.main).args
        self.real_iwyu_tool_main = iwyu_tool.main
        iwyu_tool.main = self._mock
        self.call_args = {}

    def reset(self):
        iwyu_tool.main = self.real_iwyu_tool_main

    def _mock(self, *args, **kwargs):
        for i, arg in enumerate(args):
            name = self.argspec[i]
            self.call_args[name] = arg

        self.call_args.update(kwargs)
        return 0


class IWYUToolTests(unittest.TestCase):
    def _execute(self, invocations, verbose=False, formatter=None, jobs=1):
        formatter = formatter or iwyu_tool.DEFAULT_FORMAT
        formatter = iwyu_tool.FORMATTERS.get(formatter, formatter)
        return iwyu_tool.execute(invocations, verbose, formatter, jobs)

    def setUp(self):
        self.stdout_stub = StringIO()
        iwyu_tool.sys.stdout = self.stdout_stub

    def test_from_compile_command(self):
        extra_args = ['-foo']
        invocation = iwyu_tool.Invocation.from_compile_command(
            {
                'directory': '/home/user/llvm/build',
                'command': '/usr/bin/clang++ -Iinclude file.cc',
                'file': 'file.cc'
            }, extra_args)
        self.assertEqual(
            invocation.command,
            [iwyu_tool.IWYU_EXECUTABLE, '-foo', '-Iinclude', 'file.cc'])
        self.assertEqual(invocation.cwd, '/home/user/llvm/build')

    def test_invocation(self):
        invocation = MockInvocation()
        invocation.will_return('BAR')
        self._execute([invocation])
        self.assertEqual(self.stdout_stub.getvalue(), 'BAR\n')

    def test_order_asynchronous(self):
        invocations = [MockInvocation() for _ in range(100)]
        for n, invocation in enumerate(invocations):
            invocation.will_return('BAR%d' % n)
            invocation.will_block(random.random() / 100)
        self._execute(invocations, jobs=100)
        self.assertSetEqual(
            set('BAR%d' % n for n in range(100)),
            set(self.stdout_stub.getvalue().splitlines()))

    def test_order_synchronous(self):
        invocations = [MockInvocation() for _ in range(100)]
        for n, invocation in enumerate(invocations):
            invocation.will_return('BAR%d' % n)
            invocation.will_block(random.random() / 100)
        self._execute(invocations, jobs=1)
        self.assertEqual(['BAR%d' % n for n in range(100)],
                         self.stdout_stub.getvalue().splitlines())

    def test_invalid_jobs_are_rejected_before_execution(self):
        invalid_jobs = [
            True, False, 1.0, float('nan'), float('inf'), float('-inf'),
            0, -1,
        ]
        for jobs in invalid_jobs:
            with self.subTest(api='execute', jobs=jobs):
                with self.assertRaises(ValueError):
                    self._execute([], jobs=jobs)
            with self.subTest(api='execute_json', jobs=jobs):
                with self.assertRaises(ValueError):
                    iwyu_tool.execute_json([], verbose=False, jobs=jobs)

    def test_returncode(self):
        invocation = MockInvocation()
        invocation.will_returncode(0)
        self.assertEqual(self._execute([invocation]), 0)
        invocation = MockInvocation()
        invocation.will_returncode(1)
        self.assertEqual(self._execute([invocation]), 1)
        invocation = MockInvocation()
        invocation.will_returncode(2)
        self.assertEqual(self._execute([invocation]), 2)

    def test_returncode_asynchronous(self):
        invocations = [MockInvocation() for _ in range(100)]
        for invocation in invocations:
            invocation.will_returncode(0)
            invocation.will_block(random.random() / 100)
        self.assertEqual(self._execute(invocations, jobs=100), 0)
        invocations = [MockInvocation() for _ in range(100)]
        for invocation in invocations:
            invocation.will_returncode(2)
            invocation.will_block(random.random() / 100)
        self.assertEqual(self._execute(invocations, jobs=100), 2)
        invocations = [MockInvocation() for _ in range(100)]
        for n, invocation in enumerate(invocations):
            invocation.will_returncode(6 if n == 0 else 2)
            invocation.will_block(random.random() / 100)
        self.assertEqual(self._execute(invocations, jobs=100), 6)

    def test_returncode_synchronous(self):
        invocations = [MockInvocation() for _ in range(1)]
        for invocation in invocations:
            invocation.will_returncode(0)
            invocation.will_block(random.random() / 100)
        self.assertEqual(self._execute(invocations, jobs=100), 0)
        invocations = [MockInvocation() for _ in range(1)]
        for invocation in invocations:
            invocation.will_returncode(2)
            invocation.will_block(random.random() / 100)
        self.assertEqual(self._execute(invocations, jobs=100), 2)
        invocations = [MockInvocation() for _ in range(1)]
        for n, invocation in enumerate(invocations):
            invocation.will_returncode(6 if n == 0 else 2)
            invocation.will_block(random.random() / 100)
        self.assertEqual(self._execute(invocations, jobs=100), 6)

    @unittest.skipIf(sys.platform.startswith('win'), "POSIX only")
    def test_is_subpath_of_posix(self):
        self.assertTrue(iwyu_tool.is_subpath_of('/a/b/c.c', '/a/b'))
        self.assertTrue(iwyu_tool.is_subpath_of('/a/b/c.c', '/a/b/'))
        self.assertTrue(iwyu_tool.is_subpath_of('/a/b/c.c', '/a/b/c.c'))
        self.assertFalse(iwyu_tool.is_subpath_of('/a/b/c.c', '/a/b/c'))
        self.assertFalse(iwyu_tool.is_subpath_of('/a/b/c.c', '/a/x'))
        # No case-insensitive match.
        self.assertFalse(iwyu_tool.is_subpath_of('/A/Bee/C.c', '/a/BEE'))

    @unittest.skipIf(not sys.platform.startswith('win'), "Windows only")
    def test_is_subpath_of_windows(self):
        self.assertTrue(iwyu_tool.is_subpath_of('\\a\\b\\c.c', '\\a\\b'))
        self.assertTrue(iwyu_tool.is_subpath_of('\\a\\b\\c.c', '\\a\\b\\'))
        self.assertTrue(iwyu_tool.is_subpath_of('\\a\\b\\c.c', '\\a\\b\\c.c'))
        self.assertFalse(iwyu_tool.is_subpath_of('\\a\\b\\c.c', '\\a\\b\\c'))
        self.assertFalse(iwyu_tool.is_subpath_of('\\a\\b\\c.c', '\\a\\x'))
        # Case-insensitive match.
        self.assertTrue(iwyu_tool.is_subpath_of('C:\\Bee\\C.c', 'c:\\BEE'))

    def test_from_cl_compile_command(self):
        invocation = iwyu_tool.Invocation.from_compile_command(
            {
                'directory': '/a',
                'command': 'cl.exe -I. x.cc',
                'file': 'x.cc'
            }, [])
        # Adds --driver-mode=cl if argv[0] is MSVC driver.
        self.assertEqual(
            invocation.command,
            [iwyu_tool.IWYU_EXECUTABLE, '--driver-mode=cl', '-I.', 'x.cc'])

    def test_is_msvc_driver(self):
        self.assertTrue(iwyu_tool.is_msvc_driver("cl.exe"))
        self.assertTrue(iwyu_tool.is_msvc_driver("clang-cl.exe"))
        self.assertTrue(iwyu_tool.is_msvc_driver("clang-cl"))
        self.assertFalse(iwyu_tool.is_msvc_driver("something"))

    @unittest.skipIf(not sys.platform.startswith('win'), 'Windows only')
    def test_is_msvc_driver_windows(self):
        # Case-insensitive match on Windows
        self.assertTrue(iwyu_tool.is_msvc_driver("CL.EXE"))
        self.assertTrue(iwyu_tool.is_msvc_driver("Clang-CL.exe"))
        self.assertTrue(iwyu_tool.is_msvc_driver("Clang-CL"))

    def test_split_command(self):
        self.assertEqual(['a', 'b', 'c d'],
                         iwyu_tool.split_command('a b "c d"'))

        self.assertEqual(['c', '-Idir/with spaces', 'x'],
                         iwyu_tool.split_command('c -I"dir/with spaces" x'))


class JsonProtocolTests(unittest.TestCase):
    def setUp(self):
        self.saved_stdout = iwyu_tool.sys.stdout
        self.saved_stderr = iwyu_tool.sys.stderr
        self.stdout_stub = StringIO()
        self.stderr_stub = StringIO()
        iwyu_tool.sys.stdout = self.stdout_stub
        iwyu_tool.sys.stderr = self.stderr_stub

    def tearDown(self):
        iwyu_tool.sys.stdout = self.saved_stdout
        iwyu_tool.sys.stderr = self.saved_stderr

    def test_reverse_completion_emits_database_index_order(self):
        first = MockJsonInvocation(2, block=.02, stderr='stderr-2\n')
        second = MockJsonInvocation(5, block=0, stderr='stderr-5\n')
        first._process.content = first.record()
        second._process.content = second.record()

        self.assertEqual(0, iwyu_tool.execute_json(
            [second, first], verbose=False, jobs=2))
        self.assertEqual([first.record(), second.record()],
                         self.stdout_stub.getvalue().splitlines())
        self.assertEqual('stderr-2\nstderr-5\n',
                         self.stderr_stub.getvalue())

    def test_malformed_child_output_is_atomic(self):
        malformed = MockJsonInvocation(0)
        valid = MockJsonInvocation(1)
        malformed._process.content = malformed.record() + '\nnot json\n'
        valid._process.content = valid.record()

        self.assertEqual(1, iwyu_tool.execute_json(
            [malformed, valid], verbose=False, jobs=2))
        self.assertEqual('', self.stdout_stub.getvalue())
        self.assertIn('invalid JSON', self.stderr_stub.getvalue())

    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        with self.assertRaises(iwyu_tool.ProtocolError):
            iwyu_tool._parse_json_object('{"a":1,"a":2}')
        for value in ['NaN', 'Infinity', '-Infinity']:
            with self.assertRaises(iwyu_tool.ProtocolError):
                iwyu_tool._parse_json_object('{"a":%s}' % value)
        with self.assertRaises(iwyu_tool.ProtocolError):
            iwyu_tool._parse_json_object('[]')

    def test_duplicate_record_identity_is_rejected(self):
        invocation = MockJsonInvocation(0)
        invocation._process.content = (invocation.record() + '\n' +
                                       invocation.record())
        self.assertEqual(1, iwyu_tool.execute_json(
            [invocation], verbose=False, jobs=1))
        self.assertEqual('', self.stdout_stub.getvalue())
        self.assertIn('duplicate record identity',
                      self.stderr_stub.getvalue())

    def test_metadata_and_child_record_order_are_validated(self):
        invocation = MockJsonInvocation(3)
        for key, bad_value in [
                ('index', 4),
                ('id', 'inv-000003-ffffffffffff'),
                ('translation_unit', '/src/other.cc'),
                ('command_digest', 'sha256:' + 'f' * 64)]:
            bad_metadata = json.loads(invocation.record())
            bad_metadata['invocation'][key] = bad_value
            with self.assertRaises(iwyu_tool.ProtocolError):
                iwyu_tool.parse_json_output(
                    json.dumps(bad_metadata), invocation)

        output = (invocation.record('/z.h') + '\n' +
                  invocation.record('/a.h'))
        with self.assertRaises(iwyu_tool.ProtocolError):
            iwyu_tool.parse_json_output(output, invocation)

    def test_correct_no_change_record_is_not_dropped(self):
        invocation = MockJsonInvocation(0)
        invocation._process.content = invocation.record(status='correct')
        self.assertEqual(0, iwyu_tool.execute_json(
            [invocation], verbose=False, jobs=1))
        self.assertEqual(invocation.record(status='correct') + '\n',
                         self.stdout_stub.getvalue())

    def test_successful_empty_output_is_error(self):
        invocation = MockJsonInvocation(0, stdout='', returncode=0)
        self.assertEqual(1, iwyu_tool.execute_json(
            [invocation], verbose=False, jobs=1))
        self.assertEqual('', self.stdout_stub.getvalue())
        self.assertIn('successful child produced no records',
                      self.stderr_stub.getvalue())

    def test_lf_framing_accepts_zero_or_one_terminal_lf(self):
        invocation = MockJsonInvocation(0)
        record = invocation.record()
        self.assertEqual([record], iwyu_tool.parse_json_output(
            record, invocation))
        self.assertEqual([record], iwyu_tool.parse_json_output(
            record + '\n', invocation))

    def test_empty_records_and_multiple_terminal_lf_are_rejected(self):
        invocation = MockJsonInvocation(0)
        first = invocation.record('/a.h')
        second = invocation.record('/b.h')
        for output in [
                '\n',
                first + '\n\n' + second,
                first + '\n\n',
                first + '\n \t\n' + second]:
            with self.subTest(output=repr(output)):
                with self.assertRaises(iwyu_tool.ProtocolError):
                    iwyu_tool.parse_json_output(output, invocation)

    def test_only_lf_is_a_record_separator(self):
        invocation = MockJsonInvocation(0)
        first = invocation.record('/a.h')
        second = invocation.record('/b.h')
        invalid_streams = [
            first + '\r\n',
            first + '\r' + second,
            first + '\u2028' + second,
            first + '\u2029' + second,
            '\u2028' + first,
            first + ' ' + second,
        ]
        for output in invalid_streams:
            with self.subTest(output=repr(output)):
                with self.assertRaises(iwyu_tool.ProtocolError):
                    iwyu_tool.parse_json_output(output, invocation)

    def test_unicode_line_separators_are_allowed_inside_json_strings(self):
        invocation = MockJsonInvocation(0)
        record = json.loads(invocation.record())
        record['file']['path'] = '/a\u2028b\u2029c.h'
        line = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
        self.assertEqual([line], iwyu_tool.parse_json_output(line, invocation))

    def test_invalid_framing_keeps_all_structured_output_atomic(self):
        malformed = MockJsonInvocation(0)
        valid = MockJsonInvocation(1)
        malformed._process.content = malformed.record() + '\n\n'
        valid._process.content = valid.record() + '\n'

        self.assertEqual(1, iwyu_tool.execute_json(
            [malformed, valid], verbose=False, jobs=2))
        self.assertEqual('', self.stdout_stub.getvalue())
        self.assertIn('empty record', self.stderr_stub.getvalue())

    def test_nonzero_empty_output_is_allowed(self):
        invocation = MockJsonInvocation(
            0, stdout='', stderr='native failure\n', returncode=7)
        self.assertEqual(7, iwyu_tool.execute_json(
            [invocation], verbose=False, jobs=1))
        self.assertEqual('', self.stdout_stub.getvalue())
        self.assertEqual('native failure\n', self.stderr_stub.getvalue())

    def test_whitespace_only_line_is_rejected(self):
        invocation = MockJsonInvocation(0)
        with self.assertRaises(iwyu_tool.ProtocolError):
            iwyu_tool.parse_json_output(
                invocation.record() + '\n   \n', invocation)


class JsonCommandIdentityTests(unittest.TestCase):
    def test_invocation_id_golden_vectors_and_escaping(self):
        vectors_path = os.path.join(
            os.path.dirname(__file__), 'tests', 'schema', 'output-v1',
            'invocation-id-vectors.json')
        with open(vectors_path, 'r', encoding='utf-8') as vector_file:
            vectors = json.load(vector_file)

        for vector in vectors:
            command = vector['command']
            canonical = iwyu_tool.canonical_command_bytes(
                command['directory'], command['file'], command['arguments'])
            self.assertEqual(vector['canonical'].encode('utf-8'), canonical)
            digest, invocation_id = iwyu_tool.command_identity(
                command['directory'], command['file'], command['arguments'],
                vector['index'])
            self.assertEqual(vector['command_digest'], digest)
            self.assertEqual(vector['invocation_id'], invocation_id)

    def test_response_files_are_expanded_before_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            nested = os.path.join(directory, 'nested.rsp')
            outer = os.path.join(directory, 'outer.rsp')
            with open(nested, 'w', encoding='utf-8') as response:
                response.write('-DUNICODE=grüße -c source.cc')
            with open(outer, 'w', encoding='utf-8') as response:
                response.write('-I"dir with spaces" @nested.rsp')

            entry = {
                'directory': directory,
                'file': os.path.join(directory, 'source.cc'),
                'arguments': ['clang++', '@outer.rsp'],
            }
            invocation = iwyu_tool.Invocation.from_compile_command(
                entry, [], database_index=9, output_format='json')
            expanded = ['clang++', '-Idir with spaces',
                        '-DUNICODE=grüße', '-c', 'source.cc']
            digest, invocation_id = iwyu_tool.command_identity(
                os.path.realpath(directory), os.path.realpath(entry['file']),
                expanded, 9)
            self.assertEqual(digest, invocation.command_digest)
            self.assertEqual(invocation_id, invocation.invocation_id)
            self.assertEqual([
                iwyu_tool.IWYU_EXECUTABLE,
                '-Xiwyu', '--output_format=json',
                '-Xiwyu', '--invocation_index=9',
                '-Xiwyu', '--command_digest=%s' % digest,
                '-Xiwyu', '--translation_unit=%s' %
                os.path.realpath(entry['file']).replace(os.path.sep, '/'),
            ], invocation.command[:-4])
            self.assertEqual(expanded[1:], invocation.command[-4:])

    def test_reserved_user_json_arguments_are_rejected(self):
        entry = {
            'directory': '/build',
            'file': '/src/a.cc',
            'arguments': ['clang++', '-c', '/src/a.cc'],
        }
        for reserved in iwyu_tool.RESERVED_JSON_OPTIONS:
            with self.assertRaises(ValueError):
                iwyu_tool.Invocation.from_compile_command(
                    entry, ['-Xiwyu', reserved + '=user'],
                    database_index=0, output_format='json')

    def test_every_effective_reserved_abbreviation_is_rejected(self):
        for reserved in iwyu_tool.RESERVED_JSON_OPTIONS:
            name = reserved[2:]
            for length in range(1, len(name) + 1):
                abbreviation = '--' + name[:length]
                if abbreviation in iwyu_tool.IWYU_LONG_OPTIONS:
                    effective = abbreviation
                else:
                    matches = [
                        candidate for candidate in iwyu_tool.IWYU_LONG_OPTIONS
                        if candidate.startswith(abbreviation)]
                    effective = matches[0] if len(matches) == 1 else None
                with self.subTest(reserved=reserved,
                                  abbreviation=abbreviation):
                    if effective in iwyu_tool.RESERVED_JSON_OPTIONS:
                        with self.assertRaises(ValueError):
                            iwyu_tool.reject_reserved_json_args(
                                ['-Xiwyu', abbreviation + '=forged'])
                    else:
                        iwyu_tool.reject_reserved_json_args(
                            ['-Xiwyu', abbreviation + '=not-effective'])

    def test_reserved_abbreviations_are_rejected_in_extra_args(self):
        entry = {
            'directory': '/build',
            'file': '/src/a.cc',
            'arguments': ['clang++', '-c', '/src/a.cc'],
        }
        for abbreviation in [
                '--output_f', '--invocation_in', '--invocation_id',
                '--command_d', '--translation_u']:
            with self.subTest(abbreviation=abbreviation):
                with self.assertRaises(ValueError):
                    iwyu_tool.Invocation.from_compile_command(
                        entry, ['-Xiwyu', abbreviation + '=forged'],
                        database_index=0, output_format='json')

    def test_ambiguous_and_unrelated_iwyu_prefixes_are_not_rejected(self):
        entry = {
            'directory': '/build',
            'file': '/src/a.cc',
            'arguments': ['clang++', '-c', '/src/a.cc'],
        }
        for option in [
                '--invocation_i=value', '--comm=value', '--trans=value',
                '--c=value', '--verb=3']:
            with self.subTest(option=option):
                invocation = iwyu_tool.Invocation.from_compile_command(
                    entry, ['-Xiwyu', option], database_index=0,
                    output_format='json')
                self.assertIn(option, invocation.command)

    def test_missing_response_file_is_retained_literally(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                ["clang++", "@missing.rsp", "source.cc"],
                iwyu_tool.expand_response_files(
                    ["clang++", "@missing.rsp", "source.cc"], directory))

    def test_existing_double_at_response_file_is_expanded(self):
        with tempfile.TemporaryDirectory() as directory:
            response_path = os.path.join(directory, '@arguments.rsp')
            with open(response_path, 'w', encoding='utf-8') as response:
                response.write('-DEXPANDED source.cc')
            self.assertEqual(
                ['clang++', '-DEXPANDED', 'source.cc'],
                iwyu_tool.expand_response_files(
                    ['clang++', '@@arguments.rsp'], directory))

    def test_missing_double_at_response_file_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                ['clang++', '@@missing.rsp'],
                iwyu_tool.expand_response_files(
                    ['clang++', '@@missing.rsp'], directory))

    def test_response_file_can_expose_compiler_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            response_path = os.path.join(directory, "command.rsp")
            with open(response_path, "w", encoding="utf-8") as response:
                response.write("ccache clang++ -c source.cc")
            source = os.path.join(directory, "source.cc")
            entry = {
                "directory": directory,
                "file": source,
                "arguments": ["@command.rsp"],
            }
            invocation = iwyu_tool.Invocation.from_compile_command(
                entry, [], database_index=4, output_format="json")
            expanded = ["clang++", "-c", "source.cc"]
            digest, _ = iwyu_tool.command_identity(
                os.path.realpath(directory), os.path.realpath(source),
                expanded, 4)
            self.assertEqual(digest, invocation.command_digest)
            self.assertEqual(["-c", "source.cc"], invocation.command[-2:])

    def test_response_expansion_retokenizes_nested_files_for_clang_cl(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = os.path.join(directory, 'outer.rsp')
            compiler = os.path.join(directory, 'compiler.rsp')
            flags = os.path.join(directory, 'flags.rsp')
            with open(outer, 'w', encoding='utf-8') as response:
                response.write('ccache @compiler.rsp')
            with open(compiler, 'w', encoding='utf-8') as response:
                response.write('clang-cl.exe @flags.rsp')
            with open(flags, 'w', encoding='utf-8') as response:
                response.write(
                    r'/I"C:\Program Files\SDK\include" '
                    r'/DROOT=C:\src\root source.cc')

            source = os.path.join(directory, 'source.cc')
            entry = {
                'directory': directory,
                'file': source,
                'arguments': ['@outer.rsp'],
            }
            invocation = iwyu_tool.Invocation.from_compile_command(
                entry, [], database_index=6, output_format='json')
            expanded = [
                'clang-cl.exe', r'/IC:\Program Files\SDK\include',
                r'/DROOT=C:\src\root', 'source.cc']
            digest, _ = iwyu_tool.command_identity(
                os.path.realpath(directory), os.path.realpath(source),
                expanded, 6)

            self.assertEqual(digest, invocation.command_digest)
            self.assertEqual('--driver-mode=cl', invocation.command[1])
            self.assertEqual(expanded[1:], invocation.command[-3:])

    def test_compilation_entry_reserved_iwyu_pairs_are_rejected(self):
        for reserved in [
                '--output_f', '--invocation_in', '--invocation_id',
                '--command_d', '--translation_u']:
            entry = {
                'directory': '/build',
                'file': '/src/a.cc',
                'arguments': [
                    'clang++', '-Xiwyu', reserved + '=forged', '/src/a.cc'],
            }
            with self.subTest(reserved=reserved):
                with self.assertRaises(ValueError):
                    iwyu_tool.Invocation.from_compile_command(
                        entry, [], database_index=0, output_format='json')

    def test_nested_response_reserved_iwyu_pair_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = os.path.join(directory, 'outer.rsp')
            nested = os.path.join(directory, 'nested.rsp')
            with open(outer, 'w', encoding='utf-8') as response:
                response.write('clang++ @nested.rsp source.cc')
            with open(nested, 'w', encoding='utf-8') as response:
                response.write('-Xiwyu --command_d=sha256:forged')
            entry = {
                'directory': directory,
                'file': os.path.join(directory, 'source.cc'),
                'arguments': ['@outer.rsp'],
            }
            with self.assertRaises(ValueError):
                iwyu_tool.Invocation.from_compile_command(
                    entry, [], database_index=0, output_format='json')

    def test_unpaired_reserved_compiler_option_is_not_protocol_option(self):
        entry = {
            'directory': '/build',
            'file': '/src/a.cc',
            'arguments': [
                'clang++', '--command_digest=compiler-value', '/src/a.cc'],
        }
        invocation = iwyu_tool.Invocation.from_compile_command(
            entry, [], database_index=0, output_format='json')
        self.assertEqual('--command_digest=compiler-value',
                         invocation.command[-2])

    def test_gnu_response_expansion_drops_explicit_empty_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            response_path = os.path.join(directory, 'arguments.rsp')
            with open(response_path, 'w', encoding='utf-8') as response:
                response.write('clang++ "" \'\' -DVALUE=1 source.cc')
            source = os.path.join(directory, 'source.cc')
            entry = {
                'directory': directory,
                'file': source,
                'arguments': ['@arguments.rsp'],
            }
            invocation = iwyu_tool.Invocation.from_compile_command(
                entry, [], database_index=7, output_format='json')
            expanded = ['clang++', '-DVALUE=1', 'source.cc']
            digest, _ = iwyu_tool.command_identity(
                os.path.realpath(directory), os.path.realpath(source),
                expanded, 7)

            self.assertEqual(digest, invocation.command_digest)
            self.assertEqual(expanded[1:], invocation.command[-2:])
            self.assertNotIn('', invocation.command)

    def test_indirect_clang_cl_response_retains_empty_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            command_path = os.path.join(directory, 'command.rsp')
            flags_path = os.path.join(directory, 'flags.rsp')
            with open(command_path, 'w', encoding='utf-8') as response:
                response.write('clang-cl.exe @flags.rsp')
            with open(flags_path, 'w', encoding='utf-8') as response:
                response.write('"" /DVALUE=1 "" source.cc')
            source = os.path.join(directory, 'source.cc')
            entry = {
                'directory': directory,
                'file': source,
                'arguments': ['@command.rsp'],
            }
            invocation = iwyu_tool.Invocation.from_compile_command(
                entry, [], database_index=8, output_format='json')
            expanded = ['clang-cl.exe', '', '/DVALUE=1', '', 'source.cc']
            digest, _ = iwyu_tool.command_identity(
                os.path.realpath(directory), os.path.realpath(source),
                expanded, 8)

            self.assertEqual(digest, invocation.command_digest)
            self.assertEqual('--driver-mode=cl', invocation.command[1])
            self.assertEqual(expanded[1:], invocation.command[-4:])

    def test_reserved_options_remain_legacy_passthrough(self):
        entry = {
            "directory": "/build",
            "file": "/src/a.cc",
            "arguments": [
                "clang++", "-Xiwyu", "--command_digest=legacy", "/src/a.cc"],
        }
        invocation = iwyu_tool.Invocation.from_compile_command(
            entry, ["-Xiwyu", "--invocation_in=3",
                    "-Xiwyu", "--output_f=text"])
        self.assertIn("--invocation_in=3", invocation.command)
        self.assertIn("--output_f=text", invocation.command)
        self.assertIn("--command_digest=legacy", invocation.command)


class WinSplitTests(unittest.TestCase):
    """ iwyu_tool.win_split is subtle and complex enough that it warrants a
    dedicated test suite.
    """
    def test_explicit_empty_quotes_are_arguments(self):
        self.assertEqual(['', '', 'value', ''],
                         iwyu_tool.win_split('"" "" value ""'))


    def assert_win_split(self, cmdstr, expected):
        self.assertEqual(expected,
                         iwyu_tool.win_split(cmdstr))

    def test_msdn_examples(self):
        """ Examples from below, detailing how to parse command-lines:
        https://msdn.microsoft.com/en-us/library/windows/desktop/17w5ykft.aspx
        """
        self.assert_win_split('"abc" d e',
                              ['abc', 'd', 'e'])
        self.assert_win_split(r'a\\b d"e f"g h',
                              [r'a\\b', 'de fg', 'h'])
        self.assert_win_split(r'a\\\"b c d',
                              [r'a\"b', 'c', 'd'])
        self.assert_win_split(r'a\\\\"b c" d e',
                              [r'a\\b c', 'd', 'e'])

        # Extra: odd number of backslashes before non-quote (should be
        # interpreted literally).
        self.assert_win_split(r'a\\\b d"e f"g h',
                              [r'a\\\b', 'de fg', 'h'])

    def test_trailing_backslash(self):
        """ Check that args with trailing backslash are retained. """
        self.assert_win_split('a\\ b c', ['a\\', 'b', 'c'])
        self.assert_win_split('a\\\\ b c', ['a\\\\', 'b', 'c'])

        # Last arg has dedicated handling, make sure backslashes are flushed.
        self.assert_win_split('b c a\\', ['b', 'c', 'a\\'])

    def test_cmake_examples(self):
        """ Example of observed CMake outputs that are hard to split. """
        self.assert_win_split(r'-I"..\tools\clang\tools\iwyu\inc ludes" -A',
                              [r'-I..\tools\clang\tools\iwyu\inc ludes', '-A'])

        self.assert_win_split(r'clang -Idir\\using\\os\\seps f.cc',
                              ['clang', r'-Idir\\using\\os\\seps', 'f.cc'])

        self.assert_win_split(r'clang -Idir\using\os\seps f.cc',
                              ['clang', r'-Idir\using\os\seps', 'f.cc'])

    def test_consecutive_spaces(self):
        """ Consecutive spaces outside of quotes should be folded. """
        self.assert_win_split('clang  -I.      -A',
                              ['clang', '-I.', '-A'])

        self.assert_win_split('clang  -I. \t     -A',
                              ['clang', '-I.', '-A'])


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.main = MockIwyuToolMain()

    def tearDown(self):
        self.main.reset()

    def test_argparse_args(self):
        """ Argparse arguments are forwarded to main. """
        argv = ['iwyu_tool.py', '-v', '-o', 'clang', '-j', '12', '-p', '.',
                'src1', 'src2']
        iwyu_tool._bootstrap(argv)
        self.assertEqual('.', self.main.call_args['compilation_db_path'])
        self.assertEqual(['src1', 'src2'], self.main.call_args['source_files'])
        self.assertEqual(True, self.main.call_args['verbose'])
        self.assertEqual(iwyu_tool.FORMATTERS['clang'],
                         self.main.call_args['formatter'])
        self.assertEqual(12, self.main.call_args['jobs'])
        self.assertEqual([], self.main.call_args['extra_args'])

    def test_extra_args(self):
        """ Extra arguments after '--' are forwarded to main. """
        argv = ['iwyu_tool.py', '-p', '.', '--', '-extra1', '-extra2']
        iwyu_tool._bootstrap(argv)
        self.assertEqual(['-extra1', '-extra2'],
                         self.main.call_args['extra_args'])

    def test_extra_iwyu_args(self):
        """ Extra arguments with '-Xiwyu' prefix are forwarded verbatim. """
        argv = ['iwyu_tool.py', '-p', '.', '--', '-Xiwyu', '--arg']
        iwyu_tool._bootstrap(argv)
        self.assertEqual(['-Xiwyu', '--arg'], self.main.call_args['extra_args'])

    def test_extra_args_with_sep(self):
        """ If there are multiple '--' separators, subsequent ones are forwarded
        verbatim as part of extra arguments. """
        argv = ['iwyu_tool.py', '-p', '.', '--', 'arg1', '--', 'another_arg1']
        iwyu_tool._bootstrap(argv)
        self.assertEqual(['arg1', '--', 'another_arg1'],
                         self.main.call_args['extra_args'])

    def test_json_output_mode(self):
        argv = ['iwyu_tool.py', '-o', 'json', '-p', '.']
        iwyu_tool._bootstrap(argv)
        self.assertIsNone(self.main.call_args['formatter'])
        self.assertEqual('json', self.main.call_args['output_format'])

    def test_nonpositive_jobs_are_rejected_by_cli(self):
        saved_stderr = iwyu_tool.sys.stderr
        stderr_stub = StringIO()
        iwyu_tool.sys.stderr = stderr_stub
        try:
            for output_args in ([], ['-o', 'json']):
                for jobs in ('0', '-2'):
                    argv = (['iwyu_tool.py'] + output_args +
                            ['-j', jobs, '-p', '.'])
                    with self.assertRaises(SystemExit) as raised:
                        iwyu_tool._bootstrap(argv)
                    self.assertEqual(2, raised.exception.code)
        finally:
            iwyu_tool.sys.stderr = saved_stderr
        self.assertIn('jobs must be a positive integer', stderr_stub.getvalue())
        self.assertEqual({}, self.main.call_args)


class JobValidationTests(unittest.TestCase):
    def setUp(self):
        self.saved_stderr = iwyu_tool.sys.stderr
        self.stderr_stub = StringIO()
        iwyu_tool.sys.stderr = self.stderr_stub

    def tearDown(self):
        iwyu_tool.sys.stderr = self.saved_stderr

    def test_common_main_rejects_invalid_jobs_in_both_modes(self):
        modes = [
            (iwyu_tool.DEFAULT_FORMAT,
             iwyu_tool.FORMATTERS[iwyu_tool.DEFAULT_FORMAT]),
            (iwyu_tool.JSON_FORMAT, None),
        ]
        invalid_jobs = [
            True, False, 1.0, float('nan'), float('inf'), float('-inf'),
            0, -2,
        ]
        for output_format, formatter in modes:
            for jobs in invalid_jobs:
                with self.subTest(output_format=output_format, jobs=jobs):
                    result = iwyu_tool.main(
                        '/unused/compile_commands.json', [], False, formatter,
                        jobs, 0, [], output_format)
                    self.assertEqual(1, result)
        self.assertEqual(len(modes) * len(invalid_jobs),
                         self.stderr_stub.getvalue().count(
                             'jobs must be a positive integer'))


class CompilationDBTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.path.realpath(os.getcwd())

    def test_fixup_compilation_db(self):
        """ Compilation database path canonicalization. """
        compilation_db = [
            {
                "file": "Test.cpp"
            }
        ]

        canonical = iwyu_tool.fixup_compilation_db(compilation_db)

        # Check that file path is made absolute.
        entry = canonical[0]
        self.assertEqual(os.path.join(self.cwd, 'Test.cpp'), entry['file'])

    def test_fixup_from_entry_dir(self):
        """ Compilation database abs path is based on an entry's directory. """

        # Use a root dir from uuidgen so we don't risk hitting a real path.
        compilation_db = [
            {
                "directory": "/c057f113f69311e990bf54a05050d914/foobar",
                "file": "Test.cpp"
            }
        ]

        canonical = iwyu_tool.fixup_compilation_db(compilation_db)

        # Check that the file path is relative to the directory entry,
        # not to the current directory.
        entry = canonical[0]
        self.assertEqual('/c057f113f69311e990bf54a05050d914/foobar/Test.cpp',
                         entry['file'])

    def test_unwrap_compile_command(self):
        """ Wrapping compile commands should be unwrapped. """
        compilation_db = {
            'directory': '/home/user/llvm/build',
            "command": "ccache cc -c test.c"
        }

        invocation = iwyu_tool.Invocation.from_compile_command(compilation_db, [])

        self.assertEqual(
            invocation.command,
            [iwyu_tool.IWYU_EXECUTABLE, '-c', 'test.c'])

    def test_json_keeps_original_indices_for_selected_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            skipped = os.path.join(directory, 'skipped.cc')
            selected = os.path.join(directory, 'selected.cc')
            open(skipped, 'w').close()
            open(selected, 'w').close()
            database = [
                {'directory': directory, 'file': skipped,
                 'arguments': ['clang++', '-c', skipped]},
                {'directory': directory, 'file': selected,
                 'arguments': ['clang++', '-c', selected]},
                {'directory': directory, 'file': skipped,
                 'arguments': ['clang++', '-DSECOND', '-c', skipped]},
                {'directory': directory, 'file': selected,
                 'arguments': ['clang++', '-c', selected]},
            ]
            database_path = os.path.join(directory, 'compile_commands.json')
            with open(database_path, 'w') as database_file:
                json.dump(database, database_file)

            captured = []
            original_execute_json = iwyu_tool.execute_json
            original_executable = iwyu_tool.IWYU_EXECUTABLE
            try:
                iwyu_tool.IWYU_EXECUTABLE = 'include-what-you-use'

                def capture(invocations, verbose, jobs, max_load_average):
                    captured.extend(invocations)
                    return 0

                iwyu_tool.execute_json = capture
                result = iwyu_tool.main(
                    database_path, [selected, selected], False, None, 1, 0,
                    [], 'json')
            finally:
                iwyu_tool.execute_json = original_execute_json
                iwyu_tool.IWYU_EXECUTABLE = original_executable

            self.assertEqual(0, result)
            self.assertEqual([1, 3],
                             [item.database_index for item in captured])
            self.assertEqual(captured[0].command_digest,
                             captured[1].command_digest)
            self.assertNotEqual(captured[0].invocation_id,
                                captured[1].invocation_id)


if __name__ == '__main__':
    unittest.main()
