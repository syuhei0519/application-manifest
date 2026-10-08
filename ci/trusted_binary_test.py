import copy
import hashlib
import unittest
import trusted_binary

class Binding(unittest.TestCase):
    def test_substituted_build_or_binary_is_refused(self):
        sha = 'a' * 40
        job = {'id': 7, 'name': 'security-policy-test', 'status': 'success',
               'allow_failure': False, 'ref': 'main', 'commit': {'id': sha},
               'pipeline': {'id': 9, 'project_id': 11, 'sha': sha}}
        data = b'bounded synthetic verifier'
        proof = {'schemaVersion': 1, 'projectId': 11, 'pipelineId': 9,
                 'jobId': 7, 'sourceCommit': sha,
                 'binarySha256': hashlib.sha256(data).hexdigest()}
        trusted_binary.verify(job, proof, data, 11, 9, sha)
        for field, value in [('name', 'producer'), ('status', 'failed'),
                             ('ref', 'candidate'), ('allow_failure', True), ('id', 8)]:
            changed = copy.deepcopy(job); changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                trusted_binary.verify(changed, proof, data, 11, 9, sha)
        for field, value in [('id', 10), ('project_id', 12), ('sha', 'b' * 40)]:
            changed = copy.deepcopy(job); changed['pipeline'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                trusted_binary.verify(changed, proof, data, 11, 9, sha)
        with self.assertRaises(ValueError):
            trusted_binary.verify(job, proof, data + b'swap', 11, 9, sha)
        changed = dict(proof, unexpected=True)
        with self.assertRaises(ValueError):
            trusted_binary.verify(job, changed, data, 11, 9, sha)

if __name__ == '__main__':
    unittest.main()
