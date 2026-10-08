"""GitLab由来のproducer identityをfixtureで照合する試験。networkや資格情報は不要。"""
import copy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('producer',Path(__file__).with_name('trusted-producer.py'))
producer=importlib.util.module_from_spec(spec); spec.loader.exec_module(producer)
SHA='a'*40
JOB={'id':42,'name':'trusted-render-producer','ref':'main','commit':{'id':SHA},'pipeline':{'id':21,'sha':SHA}}
MR={'state':'opened','target_branch':'main','source_project_id':7,'target_project_id':7,'sha':'b'*40}

class IdentityTest(unittest.TestCase):
    def test_valid_fixed_server_identity(self):
        producer.validate_identity(JOB,MR,7,SHA,42,21)

    def test_rejects_fork_and_changed_job_identity(self):
        cases=[('job','id',99),('job','name','mr-render'),('job','ref','feature'),
            ('mr','state','merged'),('mr','target_branch','feature'),('mr','source_project_id',8),
            ('mr','target_project_id',8),('mr','sha','../outside')]
        for owner,key,value in cases:
            with self.subTest(owner=owner,key=key):
                job=copy.deepcopy(JOB); mr=copy.deepcopy(MR)
                (job if owner=='job' else mr)[key]=value
                with self.assertRaises(ValueError):
                    producer.validate_identity(job,mr,7,SHA,42,21)

    def test_rejects_stale_verifier_and_other_pipeline(self):
        for part,key,value in [('commit','id','c'*40),('pipeline','id',22),('pipeline','sha','c'*40)]:
            with self.subTest(part=part,key=key):
                job=copy.deepcopy(JOB); job[part][key]=value
                with self.assertRaises(ValueError):
                    producer.validate_identity(job,MR,7,SHA,42,21)

if __name__=='__main__':
    unittest.main()
