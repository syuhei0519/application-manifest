import copy
import json
import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import trusted_phase2

class Inspection(unittest.TestCase):
    def test_current_native_flags_and_source_cannot_be_substituted(self):
        sha='a'*40; selection={'sourceCommit':sha,'recordSha256':'b'*64}
        proof={'selection':selection,'policyRevision':sha,'scanBytesVerified':True,
               'nativeRecordMatched':True,'nativeInspectionMatched':True,'adoptionAuthorized':False}
        with patch('trusted_phase2.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(proof).encode())) as runner:
            trusted_phase2.inspect('/fixed/consumer-scan',selection,sha)
            self.assertEqual(runner.call_args.args[0],['/fixed/consumer-scan','current-inspected'])
        changes={'selection':dict(selection,recordSha256='c'*64),'policyRevision':'c'*40,
                 'scanBytesVerified':False,'nativeRecordMatched':False,
                 'nativeInspectionMatched':False,'adoptionAuthorized':True,'unexpected':True}
        for key,value in changes.items():
            changed=copy.deepcopy(proof); changed[key]=value
            with self.subTest(key=key),patch('trusted_phase2.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(changed).encode())),self.assertRaises(ValueError):
                trusted_phase2.inspect('/fixed/consumer-scan',selection,sha)
        with patch('trusted_phase2.subprocess.run') as runner,self.assertRaises(ValueError):
            trusted_phase2.inspect('/fixed/consumer-scan',selection,'c'*40)
        runner.assert_not_called()

    def test_failure_and_timeout_never_use_old_proof(self):
        for result in [SimpleNamespace(returncode=1,stdout=b'{}'),SimpleNamespace(returncode=0,stdout=b'bad'),SimpleNamespace(returncode=0,stdout=b'x'*8193)]:
            with patch('trusted_phase2.subprocess.run',return_value=result),self.assertRaises(ValueError):
                trusted_phase2.inspect('/fixed/consumer-scan',{'sourceCommit':'a'*40},'a'*40)
        with patch('trusted_phase2.subprocess.run',side_effect=subprocess.TimeoutExpired('fixed',180)),self.assertRaises(ValueError):
            trusted_phase2.inspect('/fixed/consumer-scan',{'sourceCommit':'a'*40},'a'*40)

if __name__=='__main__':
    unittest.main()
