# merge直前の読取専用checker。ci/pre-merge-check.shが固定mainのコードから起動する。
# stdinのconsumer job ID等から成功reportとlive状態を読み、SHA・後続attempt・期限を再照合する。
# 成功JSONもmergeAuthorized=false。検証から実mergeまで原子的にlockする実装ではない。
"""固定mainからの読取専用直前確認。Ownerによるmergeは別操作。"""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

PROJECT=86247034
APP_PROJECTS=(86247025,86247033)

_attempts=None

def attempts_module():
    global _attempts
    if _attempts is None:
        spec=importlib.util.spec_from_file_location('trusted_attempts',Path(__file__).with_name('trusted_attempts.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        _attempts=module
    return _attempts

# MR/main/app mainの不変・保護状態に加え、R02対応としてscan/DB/例外/report期限を再計算する。
def validate_freshness(report,snapshot,now=None):
    if report.get('schema')!='core-platform/phase0-verification/v1' or report.get('mergeAuthorized') is not False:
        raise ValueError('fixed read-only verification report required')
    for key in ('manifestMainSha','mrSourceSha','candidateTree'):
        if not re.fullmatch('[0-9a-f]{40}',str(report.get(key,''))):
            raise ValueError('invalid fixed verification SHA')
    if (snapshot.get('mrState')!='opened' or snapshot.get('targetProtected') is not True
            or snapshot.get('sourceProject')!=PROJECT or snapshot.get('targetProject')!=PROJECT
            or snapshot.get('targetBranch')!='main' or snapshot.get('mrIid')!=report.get('mrIid')):
        raise ValueError('opened same-project MR and protected main required')
    if snapshot.get('mrSourceSha')!=report['mrSourceSha']:
        raise ValueError('MR source advanced; rerender and reverify')
    if snapshot.get('manifestMainSha')!=report['manifestMainSha']:
        raise ValueError('manifest main advanced; rerender and reverify')
    expected=report.get('sourceMains')
    if not isinstance(expected,dict) or set(expected)!={str(project) for project in APP_PROJECTS}:
        raise ValueError('both fixed application main identities required')
    if snapshot.get('sourceMains')!=expected or snapshot.get('appMainsProtected') is not True:
        raise ValueError('application main advanced/unprotected; rerender and reverify')
    attempts_module().validate_report(report,now)

# consumer成功後に追加された後続runもlive照合し、R01の古い成功再選択を防ぐ。
def validate_attempts(api,report,now=None):
    for image in report['verifiedImages']:
        current=attempts_module().inspect(api,image['phase2Selection'],image['sourceMainSha'],now)
        if current!=image['validity']:
            raise ValueError('live evidence differs; rerender and reverify')

def snapshot(api,iid):
    mr=api.json('/projects/'+str(PROJECT)+'/merge_requests/'+str(iid))
    target=api.json('/projects/'+str(PROJECT)+'/repository/branches/main')
    apps={str(project):api.json('/projects/'+str(project)+'/repository/branches/main') for project in APP_PROJECTS}
    return {'checkedAt':datetime.now(timezone.utc).isoformat(),'mrIid':mr['iid'],
        'mrState':mr['state'],'sourceProject':mr['source_project_id'],'targetProject':mr['target_project_id'],
        'targetBranch':mr['target_branch'],'targetProtected':target['protected'],
        'mrSourceSha':mr['sha'],'manifestMainSha':target['commit']['id'],
        'sourceMains':{project:branch['commit']['id'] for project,branch in apps.items()},
        'appMainsProtected':all(branch['protected'] is True for branch in apps.values())}

def validate_job(job,name,sha,pipeline):
    if (job.get('status')!='success' or job.get('name')!=name or job.get('ref')!='main'
            or job.get('commit',{}).get('id')!=sha or job.get('pipeline',{}).get('id')!=pipeline):
        raise ValueError('successful fixed-main verification job required')

# stdinを16KiBまでに制限し、依存コードを固定mainへbytes照合してからAPIを読む。
# 失敗は終了1と固定メッセージ。資格情報やprivate診断を表示しない。
def main():
    raw=sys.stdin.buffer.read(16385)
    if len(raw)>16384: raise ValueError('input bound')
    request=json.loads(raw)
    job_id=request.get('consumerJobId')
    if type(job_id) is not int or job_id<=0: raise ValueError('numeric consumer job required')
    repo=Path(__file__).resolve().parent.parent
    # 運用者はレビュー済みmainからこのscriptを読み込む。MRのfileは実行しない。
    expected=request.get('verifierSha','')
    if not re.fullmatch('[0-9a-f]{40}',expected): raise ValueError('fixed verifier required')
    for relative in ('ci/pre_merge_check.py','ci/trusted_api.py','ci/trusted_attempts.py'):
        canonical=subprocess.run(['git','-c','safe.directory='+str(repo),'-C',str(repo),'show',expected+':'+relative],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15).stdout
        if canonical!=(repo/relative).read_bytes(): raise ValueError('checker dependency differs from fixed main')
    spec=importlib.util.spec_from_file_location('trusted_api',repo/'ci/trusted_api.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    api=module.GitLab(request.get('readApiToken'))
    job=api.json('/projects/'+str(PROJECT)+'/jobs/'+str(job_id))
    report=json.loads(api.read('/projects/'+str(PROJECT)+'/jobs/'+str(job_id)+'/artifacts/.trusted/phase0-verification.json',1024**2))
    if report.get('manifestMainSha')!=expected: raise ValueError('verification uses another main')
    validate_job(job,'trusted-provenance-consumer',expected,report.get('producerPipelineId'))
    producer_id=report.get('producerJobId')
    if type(producer_id) is not int or producer_id<=0: raise ValueError('numeric producer identity required')
    producer=api.json('/projects/'+str(PROJECT)+'/jobs/'+str(producer_id))
    validate_job(producer,'trusted-render-producer',expected,report['producerPipelineId'])
    validate_attempts(api,report)
    actual=snapshot(api,report['mrIid'])
    validate_freshness(report,actual)
    print(json.dumps({'schema':'core-platform/pre-merge/v1','snapshot':actual,'consumerJobId':job_id,
        'producerJobId':producer_id,'candidateTree':report['candidateTree'],
        'validUntil':report['validUntil'],'mergeAuthorized':False},sort_keys=True))

if __name__=='__main__':
    try: main()
    except Exception:
        print('Pre-merge verification refused; rerender/reverify required; private diagnostics suppressed',file=sys.stderr)
        sys.exit(1)
