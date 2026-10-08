# consumer失敗時に古い成功reportを除去し、安全な固定stageだけを保存することを確認する。
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('trusted_consumer_failure',Path(__file__).with_name('trusted-consumer.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class Failure(unittest.TestCase):
    def test_failure_removes_positive_proof_and_excludes_private_message(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'phase0-verification.json').write_text('old positive proof')
            module.record_failure(root,'native-scan-inspection')
            self.assertFalse((root/'phase0-verification.json').exists())
            self.assertEqual(json.loads((root/'consumer-failure.json').read_text()),
                {'schemaVersion':1,'status':'failed','stage':'native-scan-inspection'})
            module.record_failure(root,'synthetic private response')
            self.assertNotIn('synthetic private response',(root/'consumer-failure.json').read_text())

if __name__=='__main__':unittest.main()
