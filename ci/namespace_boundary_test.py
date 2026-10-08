"""Linux隔離境界の明示有効化試験。CIから固定Go imageと資源制限したcontainerで実行する。
namespace_sandbox.pyへfixtureを渡し、読取専用・network遮断・環境除去を検証する。
実資格情報やclusterアクセスは不要。この成功だけで実producer jobの由来を認証したことにはならない。
"""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('namespace_sandbox',Path(__file__).with_name('namespace_sandbox.py'))
ns=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ns)

class BoundaryTest(unittest.TestCase):
    def test_private_readonly_network_and_environment(self):
        os.environ['CI_JOB_TOKEN']='non-secret-test-marker'
        os.environ['CANARY_CREDENTIAL']='non-secret-test-marker'
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'input.txt').write_text('non-secret-input')
            command=r'''test ! -e /repo; test ! -e /var/run/docker.sock;
test ! -e /etc/resolv.conf; test ! -e /sys;
test -z "${CANARY_CREDENTIAL:-}"; test -z "${CI_JOB_TOKEN:-}";
test ! -w /usr; test ! -w /input; test -r /input/input.txt;
test $(ls /proc/[0-9]* -d | wc -l) -lt 12;
python3 -c 'import socket; s=socket.socket(); s.settimeout(.2); assert s.connect_ex(("1.1.1.1",443))!=0';
printf boundary-PASS'''
            self.assertEqual(ns.run([(Path(folder),'/input',True)],command),b'boundary-PASS')

    def test_deadline_stops_process_group(self):
        with self.assertRaisesRegex(ValueError,'time/output limit'):
            ns.run([], 'sleep 5', timeout=.3)

    def test_output_bound_stops_process_group(self):
        with self.assertRaisesRegex(ValueError,'time/output limit|namespace failed'):
            ns.run([], 'yes x', timeout=5)

if __name__=='__main__':
    unittest.main()
