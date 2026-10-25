# 状態保存の整合性と同時更新の扱いを確認し、検証済み履歴が失われないようにします。
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from contract import Rejected
from record_observation import record
from test_evidence_verifier import receipt,CATALOG
from verified_state import update


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'state.json'

    def test_exclusive_atomic_update_preserves_history_and_failure(self):
        update(self.path,lambda state:state.update(managed_revisions={'fixed':'request'}))
        before=self.path.read_bytes()
        def fail(state):
            state.clear();raise ValueError('stop')
        with self.assertRaises(ValueError):update(self.path,fail)
        self.assertEqual(self.path.read_bytes(),before)
        lock=self.path.with_suffix('.json.lock');lock.touch()
        with self.assertRaises(FileExistsError):update(self.path,lambda state:state.clear())
        self.assertEqual(self.path.read_bytes(),before)

    def test_operator_receipt_ingestion_persists_verified_current_without_mutation(self):
        catalog=self.root/'catalog.json';catalog.write_text(json.dumps(CATALOG))
        settings={'catalog':str(catalog),'adapter':{'state_file':str(self.path)}}
        data,current=receipt();adapter=SimpleNamespace(observe=lambda:current,clock=lambda:1111)
        with patch('record_observation.LiveAdapter',return_value=adapter):
            result=record(settings,data)
            self.assertTrue(result['traffic_health_verified'])
            state=json.loads(self.path.read_text())
            self.assertEqual(state['verified_current']['current'],current)
            self.assertEqual(state['completion']['receipt'],data)
            data['after']['application']['status']['sync']['status']='OutOfSync'
            with self.assertRaises(Rejected):record(settings,data)
        self.assertEqual(json.loads(self.path.read_text()),state)

if __name__=='__main__':unittest.main()
