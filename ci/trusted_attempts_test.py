# R01/R02回帰試験。固定時刻/実API形状の安全fixtureのみで後続失敗→古い一式再選択や期限到達を拒否する。
# 同pipeline/retry/manual/ページ分割/証跡不足も確認し、CI/registry/資格情報を使わない。
"""実API形式のoffline fixtureで最新attemptと失効を検証する。CI・registry・負荷・資格情報へのアクセスは不要。"""
import copy
import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('attempts',Path(__file__).with_name('trusted_attempts.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
NOW=datetime(2026,10,7,0,0,tzinfo=timezone.utc)
POLICY='a'*40; PROJECT=86247033

class API:
    def __init__(self):
        self.data={};self.reads=[]
    def json(self,path):
        return json.loads(self.read(path))
    def read(self,path,limit=8*1024**2):
        self.reads.append(path)
        if path not in self.data: raise ValueError('synthetic missing safe artifact')
        value=self.data[path]
        return value if isinstance(value,bytes) else json.dumps(value).encode()

def fixture(later=None,same_pipeline=False):
    api=API();base=f'/projects/{PROJECT}'
    record={'schemaVersion':2,'service':'backend','sourceProjectId':PROJECT,'sourceCommit':POLICY,
            'imageDigest':'sha256:'+'b'*64,'buildPipelineId':10,'buildJobId':11,
            'scanPipelineId':20,'scanJobId':21,'policyRevision':POLICY,
            'decision':'passed','scanJobStatus':'success','scannedAt':(NOW-timedelta(hours=1)).isoformat(),
            'database':{'updatedAt':(NOW-timedelta(hours=2)).isoformat()},'exceptions':[]}
    def job(jid,pid,name,status,started):
        return {'id':jid,'name':name,'status':status,'ref':'main','allow_failure':False,
                'commit':{'id':POLICY},'pipeline':{'id':pid,'project_id':PROJECT,'sha':POLICY},
                'started_at':started}
    first=job(21,20,'image-scan','success',(NOW-timedelta(hours=1)).isoformat())
    inventory=[first,job(22,20,'release-record-store','success',first['started_at'])]
    pipes=[{'id':20,'ref':'main','sha':POLICY}]
    def store(rec,writer):
        raw=json.dumps(rec).encode();checksum=hashlib.sha256(raw).hexdigest()
        root=base+f'/jobs/{writer}/artifacts/.release-store/public/'
        api.data[root+'record.json']=raw
        api.data[root+'stored.json']={'schemaVersion':1,'run':{'Service':'backend','Digest':rec['imageDigest'],
                    'PipelineID':rec['scanPipelineId'],'JobID':rec['scanJobId']},
                    'recordUrl':f'https://gitlab.com/api/v4/projects/{PROJECT}/packages/generic/core-platform-backend/sha256-{rec["imageDigest"][7:]}/release-record-{rec["scanPipelineId"]}-{rec["scanJobId"]}.json',
                    'recordSha256':checksum,'decision':rec['decision'],'completedJobAuthority':True,
                    'protectedMainWriter':True,'recordWrittenLast':True,'serverDuplicateDenied':True}
        return checksum
    checksum=store(record,22)
    api.data[base+'/jobs/21']=first
    if later is not None:
        pid=20 if same_pipeline else 30
        newer=job(31,pid,'oci-image-rescan-validation','failed',(NOW-timedelta(minutes=20)).isoformat())
        later_record=dict(record,scanPipelineId=pid,scanJobId=31,decision='failed',scanJobStatus='failed')
        if later=='different-digest':later_record['imageDigest']='sha256:'+'c'*64
        if later=='success':newer['status']='success';later_record.update(decision='passed',scanJobStatus='success')
        if later=='manual':newer.update(status='manual',started_at=None)
        if later=='running':newer['status']='running'
        second=[newer,job(32,pid,'release-record-store-rescan','success',newer['started_at'])]
        if same_pipeline: inventory+=second
        else:
            pipes.insert(0,{'id':pid,'ref':'main','sha':POLICY})
            api.data[base+f'/pipelines/{pid}/jobs?include_retried=true&per_page=100']=second
        if later!='missing':store(later_record,32)
    api.data[base+'/pipelines/20/jobs?include_retried=true&per_page=100']=inventory
    api.data[base+f'/pipelines?sha={POLICY}&ref=main&order_by=id&sort=desc&per_page=100&page=1']=pipes
    selection={k:record[k] for k in ('service','sourceProjectId','sourceCommit','imageDigest',
                                    'buildPipelineId','buildJobId','scanPipelineId','scanJobId')}
    selection['recordSha256']=checksum
    return api,selection,record

class Attempts(unittest.TestCase):
    def test_selected_success_with_live_writer_bytes(self):
        api,selection,_=fixture()
        result=a.inspect(api,selection,POLICY,NOW)
        self.assertEqual(a.utc(result['validUntil']),NOW+timedelta(hours=22))
        self.assertTrue(all('/trace' not in p and '/variables' not in p for p in api.reads))

    def test_A_pass_B_fail_then_complete_A_reselection_refused(self):
        for same in (False,True):
            api,selection,_=fixture('failed',same)
            with self.subTest(same_pipeline=same),self.assertRaisesRegex(ValueError,'superseded'):
                a.inspect(api,selection,POLICY,NOW)

    def test_new_success_also_requires_explicit_latest_selection(self):
        api,selection,_=fixture('success')
        with self.assertRaisesRegex(ValueError,'superseded'):a.inspect(api,selection,POLICY,NOW)

    def test_new_success_selected_explicitly_can_restore_adoption(self):
        api,selection,_=fixture('success')
        root=f'/projects/{PROJECT}'
        api.data[root+'/jobs/31']=api.data[root+'/pipelines/30/jobs?include_retried=true&per_page=100'][0]
        raw=api.data[root+'/jobs/32/artifacts/.release-store/public/record.json']
        selection.update(scanPipelineId=30,scanJobId=31,recordSha256=hashlib.sha256(raw).hexdigest())
        a.inspect(api,selection,POLICY,NOW)

    def test_later_manual_in_older_pipeline_and_pagination_are_not_hidden(self):
        api,selection,_=fixture('failed')
        root=f'/projects/{PROJECT}'
        page=root+f'/pipelines?sha={POLICY}&ref=main&order_by=id&sort=desc&per_page=100&page='
        later=api.data[root+'/pipelines/30/jobs?include_retried=true&per_page=100']
        # 前から存在するpipelineでもmanual rescanの実行は後から起き得る。
        for j in later:j['pipeline']['id']=15
        raw=json.loads(api.data[root+'/jobs/32/artifacts/.release-store/public/record.json'])
        raw['scanPipelineId']=15
        payload=json.dumps(raw).encode()
        api.data[root+'/jobs/32/artifacts/.release-store/public/record.json']=payload
        proof=api.data[root+'/jobs/32/artifacts/.release-store/public/stored.json']
        proof['run']['PipelineID']=15;proof['recordSha256']=hashlib.sha256(payload).hexdigest()
        proof['recordUrl']=proof['recordUrl'].replace('record-30-','record-15-')
        api.data[root+'/pipelines/15/jobs?include_retried=true&per_page=100']=later
        pipes=[{'id':pid,'sha':POLICY,'ref':'main'} for pid in range(119,19,-1)]
        for p in pipes:
            if p['id']!=20:api.data[root+f'/pipelines/{p["id"]}/jobs?include_retried=true&per_page=100']=[]
        api.data[page+'1']=pipes;api.data[page+'2']=[{'id':15,'sha':POLICY,'ref':'main'}]
        with self.assertRaisesRegex(ValueError,'superseded'):a.inspect(api,selection,POLICY,NOW)
        self.assertIn(page+'2',api.reads)

    def test_other_image_does_not_revoke_selected_digest(self):
        api,selection,_=fixture('different-digest')
        a.inspect(api,selection,POLICY,NOW)

    def test_unplayed_manual_is_not_a_failed_attempt(self):
        api,selection,_=fixture('manual',True)
        a.inspect(api,selection,POLICY,NOW)

    def test_missing_and_running_later_attempt_fail_closed(self):
        for case in ('missing','running'):
            api,selection,_=fixture(case)
            with self.subTest(case=case),self.assertRaises(ValueError):a.inspect(api,selection,POLICY,NOW)

    def test_mismatch_pagination_and_artifact_tampering_fail_closed(self):
        api,selection,_=fixture();baseline=copy.deepcopy(api.data)
        root=f'/projects/{PROJECT}'
        changes=[(root+'/jobs/22/artifacts/.release-store/public/record.json',b'{}'),
                 (root+'/pipelines/20/jobs?include_retried=true&per_page=100',[]),
                 (root+f'/pipelines?sha={POLICY}&ref=main&order_by=id&sort=desc&per_page=100&page=1',[])]
        for path,value in changes:
            api.data=copy.deepcopy(baseline);api.data[path]=value
            with self.subTest(path=path),self.assertRaises(ValueError):a.inspect(api,selection,POLICY,NOW)

class Expiry(unittest.TestCase):
    def report(self,exception=None):
        _,selection,record=fixture()
        if exception:record['exceptions']=[{'expiresAt':exception.isoformat()}]
        v=a.validity(record,selection['recordSha256'],NOW)
        images=[{'phase2Selection':selection,'sourceMainSha':POLICY,'validity':v}]
        return {'verifiedAt':NOW.isoformat(),'verifiedImages':images,'validUntil':a.report_deadline(images,NOW)}

    def test_unchanged_SHA_before_and_at_database_deadline(self):
        report=self.report();a.validate_report(report,NOW+timedelta(hours=21))
        with self.assertRaises(ValueError):a.validate_report(report,NOW+timedelta(hours=22))

    def test_exception_shortens_deadline(self):
        report=self.report(NOW+timedelta(minutes=5))
        a.validate_report(report,NOW+timedelta(minutes=4))
        with self.assertRaises(ValueError):a.validate_report(report,NOW+timedelta(minutes=5))

    def test_ancient_future_missing_and_inflated_report_refused(self):
        for change in ({'verifiedAt':'2000-01-01T00:00:00Z'},
                       {'verifiedAt':(NOW+timedelta(seconds=1)).isoformat()},
                       {'validUntil':(NOW+timedelta(days=5)).isoformat()}, {'verifiedImages':None}):
            report=self.report();report.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):a.validate_report(report,NOW)
        report=self.report();del report['verifiedImages'][0]['validity']
        with self.assertRaises(ValueError):a.validate_report(report,NOW)

    def test_scan_deadline_and_record_checksum_binding(self):
        report=self.report();v=report['verifiedImages'][0]['validity']
        v['dbUpdatedAt']=v['scannedAt'];v['validUntil']=(NOW+timedelta(hours=23)).isoformat()
        report['validUntil']=v['validUntil']
        a.validate_report(report,NOW+timedelta(hours=22))
        with self.assertRaises(ValueError):a.validate_report(report,NOW+timedelta(hours=23))
        v['recordSha256']='c'*64
        with self.assertRaises(ValueError):a.validate_report(report,NOW)

if __name__=='__main__':unittest.main()
