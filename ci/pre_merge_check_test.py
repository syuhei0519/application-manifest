# MR/main SHAの変化と、SHA不変でもscan/DB/例外/reportが時間失効するケースを確認する。
# live再照合はfake APIで確認し、実mergeは実行しない。
"""source/target/applicationの個別競合を資格情報なしで模擬し、直前確認が拒否することを検証する。"""
import copy
import importlib.util
from pathlib import Path
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('checker',Path(__file__).with_name('pre_merge_check.py'))
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
A='a'*40;B='b'*40;C='c'*40;D='d'*40
NOW=datetime.now(timezone.utc)
REPORT={'schema':'core-platform/phase0-verification/v1','mergeAuthorized':False,'mrIid':20,
    'manifestMainSha':A,'mrSourceSha':B,'candidateTree':C,'sourceMains':{'86247025':C,'86247033':D},
    'verifiedAt':NOW.isoformat(),'validUntil':(NOW+timedelta(hours=24)).isoformat(),'verifiedImages':[]}
SNAPSHOT={'mrIid':20,'mrState':'opened','sourceProject':86247034,'targetProject':86247034,
    'targetBranch':'main','targetProtected':True,'mrSourceSha':B,'manifestMainSha':A,
    'sourceMains':{'86247025':C,'86247033':D},'appMainsProtected':True}
class PreMergeTest(unittest.TestCase):
    def test_unchanged_snapshot(self):checker.validate_freshness(REPORT,SNAPSHOT)
    def test_same_SHA_report_expiry_and_old_schema_are_refused(self):
        checker.validate_freshness(REPORT,SNAPSHOT,NOW+timedelta(hours=23))
        with self.assertRaises(ValueError):checker.validate_freshness(REPORT,SNAPSHOT,NOW+timedelta(hours=24))
        old=copy.deepcopy(REPORT);old['verifiedAt']='2000-01-01T00:00:00Z'
        with self.assertRaises(ValueError):checker.validate_freshness(old,SNAPSHOT,NOW)
        del old['validUntil']
        with self.assertRaises(ValueError):checker.validate_freshness(old,SNAPSHOT,NOW)
    def test_live_attempt_rechecked_after_consumer_report_success(self):
        report=copy.deepcopy(REPORT)
        report['verifiedImages']=[{'phase2Selection':{'recordSha256':'a'*64},'sourceMainSha':D,'validity':{}}]
        with patch.object(checker.attempts_module(),'inspect',side_effect=ValueError('later failed attempt')) as inspect:
            with self.assertRaises(ValueError):checker.validate_attempts(object(),report,NOW)
        inspect.assert_called_once()
    def test_each_source_target_and_application_race(self):
        for field,project in [('mrSourceSha',None),('manifestMainSha',None),('sourceMains','86247025'),('sourceMains','86247033')]:
            actual=copy.deepcopy(SNAPSHOT)
            if project:actual[field][project]='e'*40
            else:actual[field]='e'*40
            with self.subTest(field=field,project=project),self.assertRaises(ValueError):checker.validate_freshness(REPORT,actual)
    def test_closed_fork_and_unprotected_mains(self):
        for field,value in [('mrState','merged'),('sourceProject',1),('targetProject',1),('targetBranch','other'),('targetProtected',False),('appMainsProtected',False),('mrIid',21)]:
            actual=copy.deepcopy(SNAPSHOT);actual[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):checker.validate_freshness(REPORT,actual)
    def test_server_job_identity_and_pipeline(self):
        job={'name':'trusted-provenance-consumer','ref':'main','status':'success','commit':{'id':A},'pipeline':{'id':42}}
        checker.validate_job(job,'trusted-provenance-consumer',A,42)
        for field,value in [('name','mr-job'),('status','failed'),('ref','feature'),('commit',{'id':B}),('pipeline',{'id':43})]:
            actual=copy.deepcopy(job);actual[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):checker.validate_job(actual,'trusted-provenance-consumer',A,42)
if __name__=='__main__':unittest.main()
