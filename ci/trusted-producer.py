#!/usr/bin/env python3
# 保護mainで動くrender producer。.gitlab-ci.ymlのtrusted-render-producerから呼ぶ。
# GitLabの実job/MRと固定SHAを入力に、render-binding.json/checksum/producer.envを生成する。
# 後段ci/trusted-consumer.pyが独立に検証するため、producerの成功だけでは採用しない。
"""保護mainのproducer。MRの内容は検証対象データとして扱い、実行権限の根拠にしない。"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

FILES=('ci/trusted-producer.py','ci/trusted_api.py','ci/trusted-render.py','ci/namespace_sandbox.py')

# 同一project・open MR・target main・実job/pipeline/SHAを照合。forkや自己申告の別jobを拒否する。
def validate_identity(job,mr,project,sha,job_id,pipeline_id):
    if (job.get('id')!=job_id or job.get('name')!='trusted-render-producer'
            or job.get('ref')!='main' or job.get('commit',{}).get('id')!=sha
            or job.get('pipeline',{}).get('id')!=pipeline_id
            or job.get('pipeline',{}).get('sha')!=sha
            or mr.get('state')!='opened' or mr.get('target_branch')!='main'
            or mr.get('source_project_id')!=project or mr.get('target_project_id')!=project
            or not re.fullmatch('[0-9a-f]{40}',str(mr.get('sha','')))):
        raise ValueError('server job/MR identity mismatch or fork')

def git(repo,*args):
    result=subprocess.run(['git','-c','safe.directory='+str(repo),'-C',str(repo),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    if result.returncode:
        raise ValueError('fixed Git operation refused; private diagnostics suppressed')
    return result.stdout

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

# 実行コードとimport先のbytesを固定commitへ照合してから、MRをデータとしてfetch/renderする。
# MR由来のscriptをimportしない。render/parseには認証情報を渡さない。
def main():
    env=os.environ
    if env.get('CI_COMMIT_REF_PROTECTED')!='true' or env.get('CI_COMMIT_BRANCH')!='main':
        raise ValueError('protected-main producer required')
    sha=env.get('CI_COMMIT_SHA','')
    if not re.fullmatch('[0-9a-f]{40}',sha):
        raise ValueError('fixed verifier SHA required')
    ids=[]
    for key in ('CI_PROJECT_ID','CI_JOB_ID','CI_PIPELINE_ID','VERIFY_MR_IID'):
        value=env.get(key,'')
        if not re.fullmatch('[1-9][0-9]*',value):
            raise ValueError('numeric server/MR identity required')
        ids.append(int(value))
    project,job_id,pipeline_id,iid=ids
    repo=Path(env['CI_PROJECT_DIR']).resolve()
    if git(repo,'rev-parse','HEAD').decode().strip()!=sha:
        raise ValueError('checkout is not the fixed producer source')
    for path in FILES:
        if git(repo,'show',sha+':'+path)!=(repo/path).read_bytes():
            raise ValueError('producer dependency differs from trusted source')
    api_module=load('trusted_api',repo/'ci/trusted_api.py')
    api=api_module.GitLab(env.get('CI_JOB_TOKEN'),job_token=True)
    job=api.json('/job')
    mr=api.json('/projects/'+str(project)+'/merge_requests/'+str(iid))
    validate_identity(job,mr,project,sha,job_id,pipeline_id)
    branches=api.json('/projects/'+str(project)+'/repository/branches?search=%5Emain%24&per_page=100')
    if not isinstance(branches,list) or len(branches)!=1 or branches[0].get('name')!='main' or branches[0].get('protected') is not True or branches[0].get('commit',{}).get('id')!=sha:
        raise ValueError('server branch is not the current protected main')
    current=git(repo,'ls-remote','origin','refs/heads/main').decode().split()[0]
    if current!=sha:
        raise ValueError('manifest main advanced; run a fresh protected-main pipeline')
    source=mr['sha']
    git(repo,'fetch','--no-tags','origin',source)
    renderer=load('trusted_render',repo/'ci/trusted-render.py')
    output=repo/'.trusted/render-binding.json'
    renderer.main(['--repo',str(repo),'--target',sha,'--source',source,'--verifier',sha,
        '--producer-job-id',str(job_id),'--runtime','linux-userns','--output',str(output)])
    report=json.loads(output.read_bytes())
    report['serverIdentity']={'projectId':project,'pipelineId':pipeline_id,'jobId':job_id,
        'jobName':job['name'],'ref':'main','commitSha':sha,'mrIid':iid}
    payload=json.dumps(report,sort_keys=True,indent=2).encode()
    output.write_bytes(payload)
    output.with_suffix('.json.sha256').write_text(hashlib.sha256(payload).hexdigest()+'\n')
    (output.parent/'producer.env').write_text('PRODUCER_JOB_ID='+str(job_id)+'\n')
    print('fixed server identity and bounded render recorded; authenticated consumer still required')

if __name__=='__main__':
    main()
