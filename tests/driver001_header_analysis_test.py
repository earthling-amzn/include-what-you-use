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
import json, os, stat, subprocess, sys
args = sys.argv[1:]
tu = ''
for i, arg in enumerate(args[:-1]):
    if arg == '-Xiwyu' and args[i + 1].startswith('--translation_unit='):
        tu = args[i + 1].split('=', 1)[1]
command = [os.environ['REAL_ANALYZER']] + args
if os.environ['WRAPPER_MODE'] == 'extra' and tu.endswith('target.hpp'):
    command += ['-Xiwyu', '--check_also=' + os.environ['EXTRA_HEADER']]
if os.environ['WRAPPER_MODE'] == 'capture' and tu.endswith(os.environ['CAPTURE_TU']):
    with open(os.environ['CAPTURE_LOG'], 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(args) + '\\n')
result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
output = result.stdout
if os.environ['WRAPPER_MODE'] == 'skip-real' and tu.endswith('source.cc'):
    changed = []
    for line in output.decode('utf-8').splitlines():
        record = json.loads(line)
        if record.get('source_identity') == os.environ['DRIFT_HEADER']:
            record['status'] = 'skipped'
            record['diagnostics'] = [{
                'severity': 'warning',
                'code': 'configuration_unavailable',
                'message': 'wrapper selected-header skip',
                'location': {'status': 'unavailable', 'value': None},
            }]
            line = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
        changed.append(line)
    output = ('\\n'.join(changed) + ('\\n' if changed else '')).encode('utf-8')
sys.stdout.buffer.write(output)
sys.stderr.buffer.write(result.stderr)
if os.environ['WRAPPER_MODE'] == 'fail' and tu.endswith('target.hpp'):
    print('synthetic failure sentinel', file=sys.stderr)
    sys.exit(7)
if tu.endswith('source.cc'):
    target = os.environ.get('DRIFT_HEADER')
    if os.environ['WRAPPER_MODE'] == 'drift':
        with open(target, 'ab') as stream:
            stream.write(b'// drift\\n')
    elif os.environ['WRAPPER_MODE'] == 'replace':
        temporary = target + '.replacement'
        with open(target, 'rb') as source, open(temporary, 'wb') as sink:
            sink.write(source.read())
        os.replace(temporary, target)
    elif os.environ['WRAPPER_MODE'] == 'chmod':
        os.chmod(target, os.stat(target).st_mode ^ stat.S_IXUSR)
    elif os.environ['WRAPPER_MODE'] == 'symlink':
        saved = target + '.saved'
        os.replace(target, saved)
        os.symlink(saved, target)
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

        # Identical rewritten argv in distinct canonical working directories
        # are distinct profiles because every relative search input resolves
        # differently. Both contexts must analyze the same orphan header.
        cwd_root = temp / 'cwd-profiles'
        cwd_headers = cwd_root / 'headers'
        cwd_headers.mkdir(parents=True)
        cwd_target = cwd_headers / 'orphan.hpp'
        cwd_target.write_text(
            '#pragma once\n#ifdef FROM_ONE\nstruct CwdOne {};\n#else\nstruct CwdTwo {};\n#endif\n',
            encoding='utf-8')
        cwd_entries = []
        for name, macro in [('one', 'FROM_ONE'), ('two', 'FROM_TWO')]:
            working = cwd_root / name
            (working / 'include').mkdir(parents=True)
            (working / 'quote').mkdir()
            (working / 'sysroot').mkdir()
            (working / 'include' / 'forced.hpp').write_text(
                '#define %s 1\n' % macro, encoding='utf-8')
            (working / 'source.cc').write_text('int source;\n', encoding='utf-8')
            cwd_entries.append({
                'directory': str(working), 'file': str(working / 'source.cc'),
                'arguments': [
                    'clang++', '-Iinclude', '-iquote', 'quote',
                    '--sysroot=sysroot', '-include', 'forced.hpp',
                    '-c', 'source.cc'],
            })
        cwd_db_dir = cwd_root / 'db'
        cwd_db_dir.mkdir()
        cwd_db = write_database(cwd_db_dir, cwd_entries)
        cwd_run = run_driver(source_root, analyzer, [
            '-o', 'json', '--output-schema', '2', '--configuration', 'cwd',
            '-p', str(cwd_db), '--analyze-header-root', str(cwd_headers),
            '--analyze-header-glob', '*.hpp',
            '--max-synthetic-header-invocations', '2'])
        assert_ok(cwd_run, 'relative cwd profile analysis')
        cwd_records = [record for record in records(cwd_run.stdout)
                       if record['source_identity'] == str(cwd_target)]
        if len(cwd_records) != 2 or any(
                record['invocation']['index'] < 2 for record in cwd_records):
            raise AssertionError(
                'differing cwd relative profiles did not produce two synthetic records')

        # The last joined selector makes this exact .c orphan repro C, and the
        # synthetic process must contain one canonical effective selector.
        c_root = temp / 'c-language'
        c_headers = c_root / 'headers'
        c_headers.mkdir(parents=True)
        c_target = c_headers / 'orphan.h'
        c_target.write_text(
            '#pragma once\n_Static_assert(sizeof(int) > 0, "C mode");\n',
            encoding='utf-8')
        c_source = c_root / 'source.c'
        c_source.write_text('int source;\n', encoding='utf-8')
        c_db_dir = c_root / 'db'
        c_db_dir.mkdir()
        c_db = write_database(c_db_dir, [{
            'directory': str(c_root), 'file': str(c_source),
            'arguments': ['clang', '-xobjective-c++', '-xc++', '-xc',
                          '-c', 'source.c'],
        }])
        wrapper = temp / 'wrapper.py'
        capture_log = c_root / 'argv.jsonl'
        env = make_wrapper(wrapper, analyzer, 'capture')
        env.update({'CAPTURE_TU': 'orphan.h', 'CAPTURE_LOG': str(capture_log)})
        c_run = run_driver(source_root, wrapper, [
            '-o', 'json', '--output-schema', '2', '--configuration', 'c',
            '-p', str(c_db), '--analyze-header-root', str(c_headers),
            '--analyze-header-glob', '*.h',
            '--max-synthetic-header-invocations', '1'], env)
        assert_ok(c_run, 'joined C selector analysis')
        captured_argv = json.loads(capture_log.read_text(encoding='utf-8').strip())
        language_positions = [i for i, arg in enumerate(captured_argv)
                              if arg == '-x' or
                              (arg.startswith('-x') and arg != '-x' and
                               arg != '-Xiwyu')]
        if (language_positions != [captured_argv.index('-x')] or
                captured_argv[captured_argv.index('-x') + 1] != 'c' or
                any(arg in captured_argv for arg in
                    ('-xc', '-xc++', '-xobjective-c++'))):
            raise AssertionError('synthetic C command did not have one effective selector')

        # A real process receives no output-producing compiler options. Check
        # both named destinations and the entire working directory for writes.
        side_root = temp / 'side-effects'
        side_headers = side_root / 'headers'
        side_headers.mkdir(parents=True)
        side_target = side_headers / 'orphan.hpp'
        side_target.write_text('#pragma once\nstruct SideEffectFree {};\n',
                               encoding='utf-8')
        side_source = side_root / 'source.cc'
        side_source.write_text('int source;\n', encoding='utf-8')
        side_db_dir = side_root / 'db'
        side_db_dir.mkdir()
        destinations = [
            'dep.d', 'commands.json', 'diag.dia', 'profile.raw',
            'trace.json', 'opt.yaml', 'object.o', 'forwarded.json',
            'forwarded.d']
        side_db = write_database(side_db_dir, [{
            'directory': str(side_root), 'file': str(side_source),
            'arguments': [
                'clang++', '-MMD', '-MFdep.d', '-MJcommands.json',
                '-save-temps=obj', '--serialize-diagnostics=diag.dia',
                '-fprofile-instr-generate=profile.raw',
                '-ftime-trace=trace.json', '-fsave-optimization-record',
                '-foptimization-record-file=opt.yaml', '-oobject.o', '-c',
                'source.cc'],
        }])
        before_paths = sorted(str(path.relative_to(side_root))
                              for path in side_root.rglob('*'))
        side_run = run_driver(source_root, analyzer, [
            '-o', 'json', '--output-schema', '2', '--configuration', 'side',
            '-p', str(side_db), '--analyze-header-root', str(side_headers),
            '--analyze-header-glob', '*.hpp',
            '--max-synthetic-header-invocations', '1', '--',
            '-ftime-trace=forwarded.json', '-MF', 'forwarded.d'])
        assert_ok(side_run, 'side-effect-free synthetic analysis')
        after_paths = sorted(str(path.relative_to(side_root))
                             for path in side_root.rglob('*'))
        if before_paths != after_paths or any((side_root / name).exists()
                                              for name in destinations):
            raise AssertionError('synthetic analysis created output artifacts')
        if b'error:' in side_run.stderr.lower():
            raise AssertionError('synthetic analyzer hid an error on stderr')

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

        target.write_text('#pragma once\n', encoding='utf-8')
        source.write_text('#include "target.hpp"\n', encoding='utf-8')
        env = make_wrapper(wrapper, analyzer, 'skip-real')
        env['DRIFT_HEADER'] = str(target)
        skipped = run_driver(source_root, wrapper, single, env)
        if (skipped.returncode == 0 or skipped.stdout or
                b'non-terminal real status' not in skipped.stderr):
            raise AssertionError(
                'skipped real selected-header record was treated as coverage: '
                'rc=%d stdout=%r stderr=%r' %
                (skipped.returncode, skipped.stdout, skipped.stderr))

        source.write_text('int source;\n', encoding='utf-8')
        target.write_text('#pragma once\n#include "extra.hpp"\n', encoding='utf-8')
        env = make_wrapper(wrapper, analyzer, 'drift')
        env['DRIFT_HEADER'] = str(target)
        drifted = run_driver(source_root, wrapper, single, env)
        if drifted.returncode == 0 or drifted.stdout or b'changed during analysis' not in drifted.stderr:
            raise AssertionError('source drift was not rejected atomically')

        for mode in ('replace', 'chmod', 'symlink'):
            if target.is_symlink():
                target.unlink()
                (Path(str(target) + '.saved')).replace(target)
            target.write_text('#pragma once\n#include "extra.hpp"\n', encoding='utf-8')
            env = make_wrapper(wrapper, analyzer, mode)
            env['DRIFT_HEADER'] = str(target)
            changed = run_driver(source_root, wrapper, single, env)
            if (changed.returncode == 0 or changed.stdout or
                    b'changed during analysis' not in changed.stderr):
                raise AssertionError('%s metadata drift was not rejected' % mode)
        if target.is_symlink():
            target.unlink()
            (Path(str(target) + '.saved')).replace(target)

        target.write_text('#error synthetic target failure\n', encoding='utf-8')
        errored = run_driver(source_root, analyzer, single)
        if errored.returncode == 0 or errored.stdout:
            raise AssertionError('synthetic error status was not atomic')

    print('DRIVER-001 opaque tests passed')


if __name__ == '__main__':
    main()
