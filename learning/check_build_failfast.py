#!/usr/bin/env python3
"""Exercise the actual documented build block with synthetic Git/Docker only."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
SOURCE = 'd48a97851619912e7efc80c470912907ce965dc7'
IMAGE_ID = 'sha256:' + 'a' * 64
MOCK = '''
import os
from pathlib import Path
import sys
args = sys.argv[1:]
case = os.environ['FIXTURE_CASE']
if Path(sys.argv[0]).name == 'git':
    if args == ['rev-parse', 'HEAD']:
        print('b' * 40 if case == 'wrong-head' else os.environ['SOURCE'])
    elif args == ['status', '--porcelain']:
        if case == 'dirty':
            print(' M synthetic-fixture')
    else:
        raise SystemExit(90)
else:
    action = ' '.join(args[:2]) if args[0] == 'image' else args[0]
    with open(os.environ['FIXTURE_TRACE'], 'a') as trace:
        trace.write(action + '\\n')
    if action == 'build':
        Path(args[args.index('--iidfile') + 1]).write_text(os.environ['FIXTURE_ID'] + '\\n')
    elif action == 'image inspect':
        print('sha256:' + 'b' * 64 if case == 'wrong-image' else os.environ['FIXTURE_ID'])
    elif action == 'image save':
        Path(args[args.index('-o') + 1]).write_text('synthetic-not-an-image\\n')
    elif action != 'tag':
        raise SystemExit(91)
'''


def exercise(block, case):
    with tempfile.TemporaryDirectory(prefix='learning-failfast-') as tmp:
        directory = Path(tmp)
        trace = directory / 'trace'
        for name in ('git', 'docker'):
            executable = directory / name
            executable.write_text('#!' + sys.executable + '\n' + MOCK)
            executable.chmod(0o700)
        env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'],
                   SOURCE=SOURCE, SERVICE='account', DOCKERFILE='synthetic-Dockerfile',
                   OUT=str(directory), FIXTURE_CASE=case, FIXTURE_TRACE=str(trace),
                   FIXTURE_ID=IMAGE_ID)
        result = subprocess.run(['bash', '-c', block], env=env, text=True,
                                capture_output=True, check=False)
        return result.returncode, trace.read_text().splitlines() if trace.exists() else []


def main():
    block = (ROOT / 'README.md').read_text().split('```bash\n', 1)[1].split('```', 1)[0]
    for case, expected in [('wrong-head', []), ('dirty', []),
                           ('wrong-image', ['build', 'image inspect']),
                           ('success', ['build', 'image inspect', 'tag', 'image save'])]:
        code, trace = exercise(block, case)
        if (code == 0) != (case == 'success') or trace != expected:
            raise AssertionError(f'{case}: exit={code}, synthetic Docker trace={trace}')
        print(f'PASS {case}: expected exit and downstream command boundary')
    print('PASS documented Cloud block; synthetic Git/Docker only, no real image build')


if __name__ == '__main__':
    main()
