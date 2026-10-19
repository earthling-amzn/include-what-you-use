#!/usr/bin/env python3
"""Opaque real-analyzer and adversarial wrapper tests for DRIVER-001."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


def run_driver(source_root, analyzer, arguments, extra_env=None):
    env = os.environ.copy()
    env['IWYU_BINARY'] = str(analyzer)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(source_root / 'iwyu_tool.py')] + arguments,
        cwd=str(source_root), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False)


def write_database(directory, entries):
    path = directory / 'compile_commands.json'
    path.write_text(json.dumps(entries), encoding='utf-8')
    return path


def records(output):
    return [json.loads(line) for line in output.decode('utf-8').splitlines()]


def assert_ok(result, label):
    if result.returncode:
        raise AssertionError('%s failed (%d):\n%s' % (
            label, result.returncode,
            result.stderr.decode('utf-8', errors='replace')))


def make_wrapper(path, analyzer, mode):
    path.write_text('''#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[1:]
tu = ''
for i, arg in enumerate(args[:-1]):
    if arg == '-Xiwyu' and args[i + 1].startswith('--translation_unit='):
        tu = args[i + 1].split('=', 1)[1]
command = [os.environ['REAL_ANALYZER']] + args
if os.environ['WRAPPER_MODE'] == 'extra' and tu.endswith('target.hpp'):
    command += ['-Xiwyu', '--check_also=' + os.environ['EXTRA_HEADER']]
result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
if os.environ['WRAPPER_MODE'] == 'fail' and tu.endswith('target.hpp'):
    print('synthetic failure sentinel', file=sys.stderr)
    sys.exit(7)
if os.environ['WRAPPER_MODE'] == 'drift' and tu.endswith('source.cc'):
    with open(os.environ['DRIFT_HEADER'], 'ab') as stream:
        stream.write(b'// drift\\n')
sys.exit(result.returncode)
''', encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return {'REAL_ANALYZER': str(analyzer), 'WRAPPER_MODE': mode}


def main():
    if len(sys.argv) != 3:
        raise SystemExit('usage: driver001_header_analysis_test.py ANALYZER SOURCE')
    analyzer = Path(sys.argv[1]).resolve()
    source_root = Path(sys.argv[2]).resolve()

    with tempfile.TemporaryDirectory(prefix='iwyu-driver001-') as temp:
        temp = Path(temp).resolve()
        headers = temp / 'headers'
        headers.mkdir()
        orphan = headers / 'orphan.hpp'
        reached = headers / 'reached.hpp'
        reached_inline = headers / 'nonassociated.inline.hpp'
        dotted_inline = headers / 'component.part.inline.hpp'
        duplicate_a = headers / 'a'
        duplicate_b = headers / 'b'
        duplicate_a.mkdir()
        duplicate_b.mkdir()
        orphan.write_text('#pragma once\nstruct Orphan {};\n', encoding='utf-8')
        reached.write_text(
            '#pragma once\n#ifdef PROFILE_A\nstruct ReachedA {};\n#else\nstruct ReachedB {};\n#endif\n',
            encoding='utf-8')
        reached_inline.write_text('#pragma once\ninline int reached_inline() { return 1; }\n',
                                  encoding='utf-8')
        dotted_inline.write_text('#pragma once\ninline int dotted_inline() { return 2; }\n',
                                 encoding='utf-8')
        (duplicate_a / 'same.hpp').write_text('#pragma once\n', encoding='utf-8')
        (duplicate_b / 'same.hpp').write_text('#pragma once\n', encoding='utf-8')

        server = temp / 'server'
        zero = temp / 'zero'
        server.mkdir()
        zero.mkdir()
        server_a = temp / 'server_a.cc'
        server_b = temp / 'server_b.cc'
        dotted_source = temp / 'component.part.cc'
        zero_source = temp / 'zero.cc'
        server_a.write_text(
            '#include "headers/reached.hpp"\n'
            '#include "headers/nonassociated.inline.hpp"\n', encoding='utf-8')
        server_b.write_text(
            '#include "headers/reached.hpp"\n'
            '#include "headers/nonassociated.inline.hpp"\n', encoding='utf-8')
        dotted_source.write_text(
            '#include "headers/component.part.inline.hpp"\n', encoding='utf-8')
        zero_source.write_text('int zero;\n', encoding='utf-8')
        server_db = write_database(server, [
            {'directory': str(temp), 'file': str(server_a),
             'arguments': ['clang++', '-I', str(temp), '-DPROFILE_A',
                           '-c', str(server_a), '-o', str(temp / 'a.o')]},
            {'directory': str(temp), 'file': str(server_b),
             'arguments': ['clang++', '-I', str(temp), '-DPROFILE_B',
                           '-c', str(server_b), '-o', str(temp / 'b.o')]},
            {'directory': str(temp), 'file': str(dotted_source),
             'arguments': ['clang++', '-I', str(temp), '-DPROFILE_A',
                           '-c', str(dotted_source)]},
        ])
        zero_db = write_database(zero, [
            {'directory': str(temp), 'file': str(zero_source),
             'arguments': ['clang++', '-I', str(temp), '-DZERO',
                           '-c', str(zero_source)]},
        ])
        base = [
            '-o', 'json', '--output-schema', '2',
            '--database', 'server=' + str(server_db),
            '--database', 'zero=' + str(zero_db),
            '--analyze-header-root', str(headers),
            '--analyze-header-glob', '*.hpp',
            '--analyze-header-glob', '**/*.hpp',
            '--max-synthetic-header-invocations', '20',
        ]
        first = run_driver(source_root, analyzer, base)
        second = run_driver(source_root, analyzer, base)
        assert_ok(first, 'named real analysis')
        assert_ok(second, 'repeated named real analysis')
        if first.stdout != second.stdout or first.stderr != second.stderr:
            raise AssertionError('repeated runs were not byte-identical')
        output = records(first.stdout)
        configurations = [
            record['invocation']['configuration']['value'] for record in output]
        if configurations != sorted(configurations, key={'server': 0, 'zero': 1}.get):
            raise AssertionError('named stream is not configuration-grouped')
        reached_server = [record for record in output
                          if record['source_identity'] == str(reached) and
                          record['invocation']['configuration']['value'] == 'server']
        if len(reached_server) != 2:
            raise AssertionError('real incompatible macro contexts were deduplicated')
        inline_server = [
            record for record in output
            if record['source_identity'] == str(reached_inline) and
            record['invocation']['configuration']['value'] == 'server']
        if len(inline_server) != 2 or any(
                record['invocation']['index'] >= 3 for record in inline_server):
            raise AssertionError(
                'reached non-associated inline header was synthesized')
        dotted_server = [
            record for record in output
            if record['source_identity'] == str(dotted_inline) and
            record['invocation']['configuration']['value'] == 'server']
        if (len(dotted_server) != 1 or
                dotted_server[0]['invocation']['index'] != 2):
            raise AssertionError(
                'associated dotted inline header was synthesized')
        reached_zero = [record for record in output
                        if record['source_identity'] == str(reached) and
                        record['invocation']['configuration']['value'] == 'zero']
        if len(reached_zero) != 1:
            raise AssertionError('uncovered Zero header was not synthesized once')
        identity_by_index = {}
        for record in output:
            index = record['invocation']['index']
            invocation_id = record['invocation']['id']
            if index in identity_by_index and identity_by_index[index] != invocation_id:
                raise AssertionError('one index has multiple invocation IDs')
            identity_by_index[index] = invocation_id
        if len(identity_by_index.values()) != len(set(identity_by_index.values())):
            raise AssertionError('invocation IDs are not context-specific')
        if not any(record['source_identity'].endswith('/a/same.hpp') for record in output):
            raise AssertionError('first duplicate basename was lost')
        if not any(record['source_identity'].endswith('/b/same.hpp') for record in output):
            raise AssertionError('second duplicate basename was lost')

        capped = run_driver(source_root, analyzer, base[:-2] + [
            '--max-synthetic-header-invocations', '1'])
        if capped.returncode == 0 or capped.stdout:
            raise AssertionError('safety cap did not fail with zero stdout')

        adversarial = temp / 'adversarial'
        adversarial.mkdir()
        target = adversarial / 'target.hpp'
        extra = adversarial / 'extra.hpp'
        source = temp / 'source.cc'
        target.write_text('#pragma once\n#include "extra.hpp"\n', encoding='utf-8')
        extra.write_text('#pragma once\nstruct Extra {};\n', encoding='utf-8')
        source.write_text('int source;\n', encoding='utf-8')
        single_db = write_database(temp, [
            {'directory': str(temp), 'file': str(source),
             'arguments': ['clang++', '-I', str(adversarial), '-c', str(source)]},
        ])
        single = [
            '-o', 'json', '--output-schema', '2', '--configuration', 'one',
            '-p', str(single_db), '--analyze-header-root', str(adversarial),
            '--analyze-header-glob', 'target.hpp',
            '--max-synthetic-header-invocations', '4',
        ]
        wrapper = temp / 'wrapper.py'
        env = make_wrapper(wrapper, analyzer, 'extra')
        env['EXTRA_HEADER'] = str(extra)
        extra_result = run_driver(source_root, wrapper, single, env)
        assert_ok(extra_result, 'synthetic extra-record filtering')
        target_records = [record for record in records(extra_result.stdout)
                          if record['source_identity'] == str(target)]
        extra_records = [record for record in records(extra_result.stdout)
                         if record['source_identity'] == str(extra)]
        if len(target_records) != 1 or extra_records:
            raise AssertionError('synthetic extra records were not filtered')

        env = make_wrapper(wrapper, analyzer, 'fail')
        failed = run_driver(source_root, wrapper, single, env)
        if failed.returncode == 0 or failed.stdout or b'sentinel' not in failed.stderr:
            raise AssertionError('phase-2 failure was not atomic')

        target.write_text('#pragma once\n#include "extra.hpp"\n', encoding='utf-8')
        env = make_wrapper(wrapper, analyzer, 'drift')
        env['DRIFT_HEADER'] = str(target)
        drifted = run_driver(source_root, wrapper, single, env)
        if drifted.returncode == 0 or drifted.stdout or b'changed during analysis' not in drifted.stderr:
            raise AssertionError('source drift was not rejected atomically')

        target.write_text('#error synthetic target failure\n', encoding='utf-8')
        errored = run_driver(source_root, analyzer, single)
        if errored.returncode == 0 or errored.stdout:
            raise AssertionError('synthetic error status was not atomic')

    print('DRIVER-001 opaque tests passed')


if __name__ == '__main__':
    main()
