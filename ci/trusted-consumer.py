#!/usr/bin/env python3
# 保護mainの独立consumer。.gitlab-ci.ymlからproducer job IDとMR番号を受ける。
# render/input/job/native scan/registry/最新attempt/期限を照合して.trusted/phase0-verification.jsonを保存。
# 成功でもmergeAuthorized=false。次の入口はci/pre_merge_check.py、R01/R02対応はdocs/review-r01-r02.md。
"""保護mainから検査済みreleaseを独立検証する。"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone

def git(repo,*args):
    result=subprocess.run(['git','-c','safe.directory='+str(repo),'-C',str(repo),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    if result.returncode:
        raise ValueError('fixed Git operation refused; private diagnostics suppressed')
    return result.stdout

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

FAILURE_STAGE="identity"

# 失敗時は以前の成功reportを消し、許可された固定stageだけを保存する。raw応答や例外の秘密情報は出さない。
def record_failure(root,stage):
    allowed={'identity','render-binding','trusted-binary','native-scan-inspection',
             'registry-identity','phase0-source-artifact','latest-attempt-validity'}
    if stage not in allowed:
        stage='identity'
    root.mkdir(exist_ok=True)
    (root/'phase0-verification.json').unlink(missing_ok=True)
    (root/'consumer-failure.json').write_text(json.dumps({
        'schemaVersion':1,'status':'failed','stage':stage}))

def main():
    global FAILURE_STAGE
    env=os.environ; repo=Path(env['CI_PROJECT_DIR']).resolve()
    if env.get('CI_COMMIT_REF_PROTECTED')!='true' or env.get('CI_COMMIT_BRANCH')!='main':
        raise ValueError('protected-main consumer required')
    verifier=env.get('CI_COMMIT_SHA','')
    if not re.fullmatch('[0-9a-f]{40}',verifier):
        raise ValueError('fixed verifier SHA required')
    ids=[]
    for key in ('CI_PROJECT_ID','CI_PIPELINE_ID','VERIFY_MR_IID','PRODUCER_JOB_ID'):
        value=env.get(key,'')
        if not re.fullmatch('[1-9][0-9]*',value):
            raise ValueError('numeric server identity required')
        ids.append(int(value))
    project,pipeline,iid,producer_id=ids
    # この入口とimport先はレビュー済み保護mainからだけ実行する。
    if git(repo,'rev-parse','HEAD').decode().strip()!=verifier:
        raise ValueError('consumer checkout SHA mismatch')
    # 検証器とimportする依存fileを同一固定main commitへbytes照合。コメント変更でもこのsource identityは変わる。
    for path in ('ci/trusted-consumer.py','ci/trusted_api.py','ci/trusted_contract.py','ci/trusted-registry.py','ci/trusted-render.py','ci/trusted_binary.py','ci/trusted_phase2.py','ci/trusted_attempts.py'):
        if git(repo,'show',verifier+':'+path)!=(repo/path).read_bytes():
            raise ValueError('consumer dependency differs from fixed trusted source')
    renderer=load('trusted_render',repo/'ci/trusted-render.py')
    contract=load('trusted_contract',repo/'ci/trusted_contract.py')
    api=load('trusted_api',repo/'ci/trusted_api.py').GitLab(env.get('MANIFEST_VERIFY_READ_API_TOKEN'))
    job=api.json('/projects/'+str(project)+'/jobs/'+str(producer_id))
    mr=api.json('/projects/'+str(project)+'/merge_requests/'+str(iid))
    branch=api.json('/projects/'+str(project)+'/repository/branches/main')
    if branch.get('protected') is not True or branch.get('commit',{}).get('id')!=verifier:
        raise ValueError('server main protection or SHA changed')
    FAILURE_STAGE='render-binding'
    prefix='/projects/'+str(project)+'/jobs/'+str(producer_id)+'/artifacts/.trusted/render-binding.json'
    payload=api.read(prefix)
    checksum=api.read(prefix+'.sha256',128).decode()
    report,changed,removed=contract.binding(payload,checksum,job,mr,project,pipeline,verifier,producer_id)
    contract.require_inspected_candidate(report['renders']['candidate']['inventory']['entries'])
    if renderer.git(repo,'ls-remote','origin','refs/heads/main').decode().split()[0]!=verifier:
        raise ValueError('manifest main advanced; rerun verification')
    renderer.git(repo,'fetch','--no-tags','origin',report['sourceSha'])
    candidate=renderer.git(repo,'merge-tree','--write-tree',verifier,report['sourceSha']).decode().splitlines()[0]
    if candidate!=report.get('candidateTree'):
        raise ValueError('merge candidate tree mismatch')
    # producerが示す入力とmerge候補を独立に再構成。temporary directoryは例外時もcontext managerが回収する。
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        trusted=renderer.snapshot(repo,verifier,root/'trusted',('tools/inventory','tools/release-record'))
        if trusted!=report.get('trustedInputs'):
            raise ValueError('fixed verifier input mismatch')
        for name,tree in (('before',verifier),('candidate',candidate)):
            inputs=renderer.snapshot(repo,tree,root/name,('charts','environments/local'))
            value=report['renders'][name]
            input_hash=hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if inputs!=value.get('inputs') or input_hash!=value.get('inputSha256'):
                raise ValueError('render input binding mismatch')
    reason=env.get('ROLLBACK_REASON','').strip()
    rollback=bool(reason)
    if rollback and (len(reason)<10 or not mr.get('source_branch','').startswith('rollback/local/')):
        raise ValueError('rollback requires explicit branch and documented reason')
    # この固定保護main pipelineがbuildしたbinaryだけを認証して使う。
    FAILURE_STAGE='trusted-binary'
    security_jobs=api.json('/projects/'+str(project)+'/pipelines/'+str(pipeline)+'/jobs?per_page=100')
    security=[j for j in security_jobs if j.get('name')=='security-policy-test']
    if len(security_jobs)>=100 or len(security)!=1:
        raise ValueError('one fixed verifier build required')
    binary_proof=json.loads(api.read('/projects/'+str(project)+'/jobs/'+str(security[0]['id'])+'/artifacts/.security/consumer-scan-binding.json',8192))
    load('trusted_binary',repo/'ci/trusted_binary.py').verify(security[0],binary_proof,
        (repo/'.security/consumer-scan').read_bytes(),project,pipeline,verifier)
    registry_module=load('trusted_registry',repo/'ci/trusted-registry.py')
    source_mains={}
    for entry in report['renders']['candidate']['inventory']['entries']+removed:
        proof=contract.classify(entry)
        if proof is not None and str(proof['projectId']) not in source_mains:
            source_mains[str(proof['projectId'])]=api.json('/projects/'+str(proof['projectId'])+'/repository/branches/main')['commit']['id']
    attempts=load('trusted_attempts',repo/'ci/trusted_attempts.py')
    verified=[]; seen=set()
    for entry,fresh in [(entry,True) for entry in changed]+[(entry,False) for entry in removed]:
        proof=contract.classify(entry)
        if proof is None:
            continue
        key=(proof['projectId'],proof['commit'],proof.get('pipelineId'),proof['digest'],
             json.dumps(proof.get('phase2Selection'),sort_keys=True),fresh)
        if key in seen:
            continue
        seen.add(key)
        if 'phase2Selection' in proof:
            if not fresh:
                raise ValueError('historical Phase 2 removal requires separate rooted-before provenance')
            source_main=source_mains[str(proof['projectId'])]
            FAILURE_STAGE='native-scan-inspection'
            native=load('trusted_phase2',repo/'ci/trusted_phase2.py').inspect(
                repo/'.security/consumer-scan',proof['phase2Selection'],source_main,rollback)
            upper=proof['service'].upper()
            registry=registry_module.Registry(proof['service'],env.get(upper+'_REGISTRY_READ_USER'),env.get(upper+'_REGISTRY_READ_PASSWORD'))
            FAILURE_STAGE='registry-identity'
            registry.verify(proof)
            FAILURE_STAGE='latest-attempt-validity'
            # R01/R02修正後の追加ゲート。同policy/digestの後続attemptをlive APIで確認し、古い成功の再選択を拒否。
            # scan/DB/例外の最短期限をreportへ結合。Go consumer単体のrun照合とは別の責任。
            validity=attempts.inspect(api,proof['phase2Selection'],source_main)
            verified.append({**proof,'sourceMainSha':source_main,'nativeScanInspection':native,
                             'freshnessRequired':True,'validity':validity})
            continue
        raise ValueError('uninspected legacy source artifact path has been retired')
    verified_at=datetime.now(timezone.utc)
    result={'schema':'core-platform/phase0-verification/v1','manifestMainSha':verifier,'mrSourceSha':report['sourceSha'],
        'mrIid':iid,'producerJobId':producer_id,'producerPipelineId':pipeline,'renderReportSha256':checksum.strip(),
        'provenanceRequired':report['provenanceRequired'],'verifiedImages':verified,'rollbackReason':reason or None,
        'candidateTree':candidate,'sourceMains':source_mains,'verifiedAt':verified_at.isoformat(),
        'validUntil':attempts.report_deadline(verified,verified_at),
        'mergeAuthorized':False}
    # 生成したreport自身も時刻/期限の整合を再検証してから保存。失敗ならmain末尾で安全な失敗reportへ切り替える。
    attempts.validate_report(result,verified_at)
    output=repo/'.trusted/phase0-verification.json'
    output.write_text(json.dumps(result,sort_keys=True,indent=2))
    print('Source, scan and registry binding verified; merge remains a separate operation')

if __name__=='__main__':
    try:
        main()
    except Exception:
        # 公開するのは固定stageだけ。private応答body/資格情報/子process出力/例外メッセージは除外する。
        root=Path(__file__).resolve().parent.parent/'.trusted'
        record_failure(root,FAILURE_STAGE)
        raise SystemExit('Trusted consumer refused; safe failure stage recorded') from None
