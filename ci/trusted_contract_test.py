"""実inventory形式と不正なPhase 0 provenance fixtureを入力に、契約違反の拒否を確認する。networkは不要。"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('contract',Path(__file__).with_name('trusted_contract.py'))
contract=importlib.util.module_from_spec(spec); spec.loader.exec_module(contract)
INVENTORY=json.loads((Path(__file__).parent/'fixtures/inventory.json').read_text(encoding='utf-8-sig'))
SHA='a'*40

def fixture():
    mr={'iid':12,'sha':'b'*40,'state':'opened','target_branch':'main','source_project_id':7,'target_project_id':7}
    job={'id':42,'name':'trusted-render-producer','status':'success','ref':'main','commit':{'id':SHA},'pipeline':{'id':21,'sha':SHA}}
    report={'schema':'core-platform/render-binding/v1','eligibleForAuthenticatedVerification':True,
        'verifierSha':SHA,'targetSha':SHA,'sourceSha':mr['sha'],'producerJobId':'42','provenanceRequired':False,
        'serverIdentity':{'projectId':7,'pipelineId':21,'jobId':42,'jobName':job['name'],'ref':'main','commitSha':SHA,'mrIid':12},
        'renders':{'before':{'inventory':copy.deepcopy(INVENTORY)},'candidate':{'inventory':copy.deepcopy(INVENTORY)}}}
    return report,job,mr

def validate(report,job,mr):
    payload=json.dumps(report).encode()
    return contract.binding(payload,hashlib.sha256(payload).hexdigest(),job,mr,7,21,SHA,42)

class ContractTest(unittest.TestCase):
    def phase2_entry(self,service='frontend'):
        entry=next(copy.deepcopy(item) for item in INVENTORY['entries'] if item['repository'].endswith('/'+service+'-app'))
        project=str(contract.APPS[entry['repository']][1])
        root='https://gitlab.com/api/v4/projects/'+project+'/packages/generic/core-platform-'+service+'/sha256-'+entry['digest'][7:]+'/'
        entry['release']={'account.lab/release-schema-version':'2','account.lab/image-digest':entry['digest'],
            'account.lab/source-project-id':project,'account.lab/source-commit':entry['tag'],
            'account.lab/build-pipeline-id':'10','account.lab/build-job-id':'11',
            'account.lab/scan-pipeline-id':'100','account.lab/scan-job-id':'101',
            'account.lab/release-record-url':root+'release-record-100-101.json',
            'account.lab/release-record-sha256':'a'*64,'account.lab/sbom-url':root+'sbom-100-101.cdx.json',
            'account.lab/sbom-sha256':'b'*64}
        return entry

    def test_phase2_data_selection_preserves_original_build_and_current_scan(self):
        proof=contract.classify(self.phase2_entry())
        selected=proof['phase2Selection']
        self.assertEqual((selected['buildPipelineId'],selected['buildJobId']),(10,11))
        self.assertEqual((selected['scanPipelineId'],selected['scanJobId']),(100,101))
        self.assertNotIn('pipelineId',proof) # Cannot enter the old pipeline contract.

    def test_phase2_rejects_alias_run_checksum_unknown_and_service_mismatch(self):
        original=self.phase2_entry()
        changes=[('account.lab/sbom-url',original['release']['account.lab/sbom-url'].replace('100-101','100-102')),
            ('account.lab/release-record-url',original['release']['account.lab/release-record-url']+'?latest=true'),
            ('account.lab/release-record-sha256','a'*63),('account.lab/source-project-id','86247033'),
            ('account.lab/scan-job-id','01'),('account.lab/build-job-id','9223372036854775808'),
            ('account.lab/pipeline-id','10'),('account.lab/release-schema-version','1')]
        for key,value in changes:
            with self.subTest(key=key):
                entry=copy.deepcopy(original);entry['release'][key]=value
                with self.assertRaises(ValueError):contract.classify(entry)
        entry=copy.deepcopy(original);entry['repository']=entry['repository'].replace('/frontend-app','/backend-app')
        with self.assertRaises(ValueError):contract.classify(entry)

    def test_backend_phase2_keeps_build_scan_and_project_binding(self):
        entry=self.phase2_entry('backend')
        proof=contract.classify(entry)
        self.assertEqual((proof['service'],proof['projectId']),('backend',86247033))
        self.assertEqual(proof['phase2Selection']['buildJobId'],11)
        self.assertEqual(proof['phase2Selection']['scanJobId'],101)
        self.assertNotIn('pipelineId',proof)
        for key,value in [('account.lab/source-project-id','86247025'),
                          ('account.lab/sbom-url',entry['release']['account.lab/sbom-url'].replace('100-101','100-102'))]:
            changed=copy.deepcopy(entry);changed['release'][key]=value
            with self.assertRaises(ValueError):contract.classify(changed)

    def test_candidate_cannot_restore_uninspected_application_even_if_unchanged(self):
        apps=[self.phase2_entry(service) for service in ('frontend','backend')]
        postgres=next(copy.deepcopy(entry) for entry in INVENTORY['entries'] if entry['repository']==contract.POSTGRES[0])
        contract.require_inspected_candidate(apps+[postgres])
        for service in ('frontend','backend'):
            legacy=next(copy.deepcopy(entry) for entry in INVENTORY['entries'] if entry['repository'].endswith('/'+service+'-app'))
            with self.subTest(service=service):
                with self.assertRaisesRegex(ValueError,'inspected Phase 2'):
                    contract.require_inspected_candidate(apps+[legacy])
    def test_actual_render_inventory_canonical_checksum(self):
        self.assertGreater(len(contract.inventory(INVENTORY)),2)

    def test_config_only_skips_provenance_with_fixed_binding(self):
        report,job,mr=fixture()
        _,changed,removed=validate(report,job,mr)
        self.assertEqual((changed,removed),([],[]))

    def test_rejects_unknown_repository_project_url_and_unfixed_digest(self):
        entry=next(copy.deepcopy(item) for item in INVENTORY['entries'] if item['repository'] in contract.APPS)
        changes=[('repository','registry.gitlab.com/other/project'),('digest','latest'),('kind','UnknownController')]
        for key,value in changes:
            with self.subTest(key=key):
                changed=copy.deepcopy(entry); changed[key]=value
                with self.assertRaises(ValueError): contract.classify(changed)
        for key,value in [('account.lab/source-project-id','999'),('account.lab/pipeline-url','https://external.example/credential-target'),('account.lab/source-commit','../x')]:
            with self.subTest(key=key):
                changed=copy.deepcopy(entry); changed['release'][key]=value
                with self.assertRaises(ValueError): contract.classify(changed)

    def test_rejects_test_only_stale_source_and_other_producing_job(self):
        cases=[('eligibleForAuthenticatedVerification',False),('sourceSha','c'*40),('producerJobId','99'),('targetSha','c'*40)]
        for key,value in cases:
            with self.subTest(key=key):
                report,job,mr=fixture(); report[key]=value
                with self.assertRaises(ValueError): validate(report,job,mr)
        report,job,mr=fixture(); job['status']='running'
        with self.assertRaises(ValueError): validate(report,job,mr)

    def test_rejects_artifact_and_inventory_checksum_substitution(self):
        report,job,mr=fixture(); payload=json.dumps(report).encode()
        with self.assertRaisesRegex(ValueError,'artifact checksum'):
            contract.binding(payload,'0'*64,job,mr,7,21,SHA,42)
        report['renders']['candidate']['inventory']['entries'][0]['name']='changed-with-old-checksum'
        with self.assertRaisesRegex(ValueError,'inventory checksum'):
            validate(report,job,mr)

    def test_source_main_freshness_and_explicit_rollback_exception(self):
        entry=next(item for item in INVENTORY['entries'] if item['repository'] in contract.APPS)
        proof=contract.classify(entry)
        pipeline={'id':proof['pipelineId'],'status':'success','ref':'main','sha':proof['commit']}
        contract.source_pipeline(pipeline,proof,proof['commit'])
        with self.assertRaisesRegex(ValueError,'advanced'):
            contract.source_pipeline(pipeline,proof,'c'*40)
        contract.source_pipeline(pipeline,proof,'c'*40,rollback=True)
        pipeline['status']='failed'
        with self.assertRaises(ValueError): contract.source_pipeline(pipeline,proof,'c'*40,rollback=True)

if __name__=='__main__':
    unittest.main()
