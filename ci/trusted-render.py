#!/usr/bin/env python3
# 固定検証器によるmerge候補のrender/inventory生成。ci/trusted-producer.pyが呼ぶ。
# Chart/valuesの入力bytesと生成YAML・全PodSpec imageを束ね、checksum付きreportを返す。
# 隔離runtimeはci/namespace_sandbox.py、inventory parserはtools/inventory/main.go。
"""固定ソースからの検証器。render/parseの隔離実行へ資格情報を渡さない。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid
import importlib.util

HELM = 'alpine/helm:4.2.4@sha256:76c375eed56144c68d6197c55bc5a4552fb42002190b796729901cbab3ae6e51'
GO = 'golang:1.27.1-bookworm@sha256:648f440f42a0958804efb24df176f806f9d353b41f1c0627f666428e40310f6b'
RUNTIME='docker'
NS=None

def digest(data):
    return hashlib.sha256(data).hexdigest()

def git(repo, *args):
    result=subprocess.run(['git', '-c', 'safe.directory='+str(repo).replace('\\','/'), '-C', str(repo), *args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    if result.returncode:
        raise ValueError('Git operation failed; private diagnostics suppressed')
    return result.stdout

# Git treeから通常fileだけを抽出し、path/サイズを制限する。作業directoryの任意ファイルを混ぜない。
def snapshot(repo, tree, destination, prefixes):
    files = {}
    for item in git(repo, 'ls-tree', '-r', '-z', tree).split(b'\0'):
        if not item:
            continue
        header, raw_path = item.split(b'\t', 1)
        name = raw_path.decode('utf-8')
        if not any(name.startswith(p+'/') for p in prefixes):
            continue
        mode, kind, oid = header.decode().split()
        if mode not in ('100644', '100755') or kind != 'blob' or '\\' in name or '..' in Path(name).parts:
            raise ValueError('non-regular/unsafe input: '+name)
        data = git(repo, 'cat-file', 'blob', oid)
        if len(data) > 4*1024*1024 or sum(x['size'] for x in files.values())+len(data)>32*1024*1024:
            raise ValueError('input size limit')
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[name] = {'sha256':digest(data), 'size':len(data)}
    if not files:
        raise ValueError('empty snapshot')
    return files

# 認証情報・networkなし、read-only filesystemと資源/時間/出力上限でrenderする。
# finallyで一時containerの除去を試み、エラー時に無制限の診断を保持しない。
def sandbox(image, mounts, command, timeout=90, compiler=False):
    if RUNTIME=='linux-userns':
        return NS.run(mounts,command,timeout=timeout,compiler=compiler)
    name = 'pe003a-'+uuid.uuid4().hex
    memory='1g' if compiler else '512m'
    tmp_size='512m' if compiler else '64m'
    args = ['docker', 'run', '--rm', '--name', name, '--network=none', '--read-only', '--cpus=2', '--memory='+memory, '--pids-limit=64', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--tmpfs=/tmp:rw,noexec,nosuid,size='+tmp_size, '--entrypoint=sh']
    for source,target,readonly in mounts:
        args += ['--mount', 'type=bind,source='+str(source)+',target='+target+(',readonly' if readonly else '')]
    args += [image, '-ec', command]
    # 出力をfileへ上限付きで記録し、悪意あるHelm診断でhost RAMを使い尽くさせない。
    with tempfile.TemporaryDirectory(prefix='pe003a-output-') as folder:
        out=Path(folder)/'stdout'; err=Path(folder)/'stderr'
        try:
            with out.open('wb') as o, err.open('wb') as e:
                proc=subprocess.Popen(args, stdout=o, stderr=e)
                deadline=time.monotonic()+timeout
                while proc.poll() is None:
                    if time.monotonic()>deadline or out.stat().st_size+err.stat().st_size>2*1024*1024:
                        proc.kill(); proc.wait(); raise ValueError('sandbox time/output limit')
                    time.sleep(0.1)
            if out.stat().st_size+err.stat().st_size>2*1024*1024:
                raise ValueError('sandbox output limit')
            if proc.returncode:
                raise ValueError('sandbox failed: '+err.read_text(errors='replace')[:2000])
            return out.read_bytes()
        finally:
            subprocess.run(['docker','rm','--force',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)

# target/source/verifierを完全SHAで固定。merge候補treeを生成し、before/candidateを同じ検証器で比較する。
def main(argv=None):
    global RUNTIME, NS
    p=argparse.ArgumentParser()
    p.add_argument('--repo',type=Path,required=True)
    for field in ('target','source','verifier'):
        p.add_argument('--'+field,required=True)
    p.add_argument('--producer-job-id',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--test-only',action='store_true')
    p.add_argument('--runtime',choices=('docker','linux-userns'),default='docker')
    a=p.parse_args(argv); a.repo=a.repo.resolve(); RUNTIME=a.runtime
    for value in (a.target,a.source,a.verifier):
        if not re.fullmatch('[0-9a-f]{40}',value):
            p.error('full SHA required')
    if not re.fullmatch('[0-9]+',a.producer_job_id):
        p.error('numeric producing job ID required')
    if not a.test_only:
        if (os.environ.get('CI_COMMIT_REF_PROTECTED') != 'true'
                or os.environ.get('CI_COMMIT_BRANCH') != 'main'
                or os.environ.get('CI_COMMIT_SHA') != a.verifier
                or os.environ.get('CI_JOB_ID') != a.producer_job_id
                or a.producer_job_id == '0'):
            raise ValueError('non-test output requires protected-main producing job identity; operator runs must use --test-only')
        git(a.repo,'merge-base','--is-ancestor',a.verifier,'origin/main')
        canonical=git(a.repo,'show',a.verifier+':ci/trusted-render.py')
        if canonical!=Path(__file__).read_bytes():
            raise ValueError('running verifier is not the fixed trusted source')
    if RUNTIME=='linux-userns':
        module=Path(__file__).with_name('namespace_sandbox.py')
        if not a.test_only and git(a.repo,'show',a.verifier+':ci/namespace_sandbox.py')!=module.read_bytes():
            raise ValueError('namespace boundary is not from fixed trusted source')
        spec=importlib.util.spec_from_file_location('namespace_sandbox',module)
        NS=importlib.util.module_from_spec(spec); spec.loader.exec_module(NS)
    merge=git(a.repo,'merge-tree','--write-tree',a.target,a.source).decode().splitlines()[0]
    if not re.fullmatch('[0-9a-f]{40}',merge):
        raise ValueError('invalid merge candidate tree')
    with tempfile.TemporaryDirectory(prefix='pe003a-') as folder:
        root=Path(folder); trusted=root/'trusted'; trusted.mkdir(); output=root/'binary'; output.mkdir()
        tool={}
        if RUNTIME=='linux-userns':
            tool={'helmArchiveSha256':NS.HELM_ARCHIVE_SHA256,'helmBinarySha256':NS.prepare_helm(output/'helm'),'limits':NS.cgroup_limits()}
        trusted_files=snapshot(a.repo,a.verifier,trusted,('tools/inventory','tools/release-record'))
        sandbox(GO,[(trusted/'tools/inventory','/src',True),(output,'/out',False)],'cd /src; GOCACHE=/tmp/cache GOPATH=/tmp/go CGO_ENABLED=0 go build -p 2 -mod=vendor -o /out/inventory .',compiler=True)
        report={'schema':'core-platform/render-binding/v1','eligibleForAuthenticatedVerification':not a.test_only,'targetSha':a.target,'sourceSha':a.source,'candidateTree':merge,'verifierSha':a.verifier,'producerJobId':a.producer_job_id,'helmImage':HELM if RUNTIME=='docker' else None,'goImage':GO,'trustedInputs':trusted_files,'binarySha256':digest((output/'inventory').read_bytes()),'renders':{}}
        report['runtime']=RUNTIME; report['runtimeTools']=tool
        for name,tree in (('before',a.target),('candidate',merge)):
            inputs=root/name; inputs.mkdir(); files=snapshot(a.repo,tree,inputs,('charts','environments/local'))
            for lock in inputs.glob('charts/*/Chart.lock'):
                # 依存Chartは同梱された固定入力を使う。Helmにdownloadさせない。
                if not (lock.parent/'charts').exists():
                    raise ValueError('dependency archive not present: '+str(lock))
            helm='/trusted/helm' if RUNTIME=='linux-userns' else 'helm'
            raw=sandbox(HELM,[(inputs,'/input',True),(output,'/trusted',True)],'cd /input; export HELM_PLUGINS=/nonexistent HELM_CACHE_HOME=/tmp/helm/cache HELM_CONFIG_HOME=/tmp/helm/config HELM_DATA_HOME=/tmp/helm/data; for chart in frontend backend postgresql; do '+helm+' template "$chart" "charts/$chart" --namespace account -f "environments/local/$chart.yaml" >> /tmp/render.yaml || exit 1; printf "\\n---\\n" >> /tmp/render.yaml; done; /trusted/inventory < /tmp/render.yaml')
            inventory=json.loads(raw)
            report['renders'][name]={'inputs':files,'inputSha256':digest(json.dumps(files,sort_keys=True,separators=(',',':')).encode()),'inventory':inventory}
        before=report['renders']['before']['inventory']; after=report['renders']['candidate']['inventory']
        report['provenanceRequired']=before['inventorySha256']!=after['inventorySha256']
        def identity(entry):
            return '/'.join(entry[k] for k in ('namespace','kind','name','podSpecPath','containerType','containerName'))
        old={identity(x):x for x in before['entries']}; new={identity(x):x for x in after['entries']}
        report['diff']={'added':[new[k] for k in sorted(new.keys()-old.keys())],
                        'removed':[old[k] for k in sorted(old.keys()-new.keys())],
                        'changed':[{'before':old[k],'after':new[k]} for k in sorted(old.keys()&new.keys()) if old[k]!=new[k]]}
        payload=json.dumps(report,sort_keys=True,indent=2).encode()
        if len(payload)>8*1024**2:
            raise ValueError('render report size limit')
        a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_bytes(payload)
        a.output.with_suffix(a.output.suffix+'.sha256').write_text(digest(payload)+'\n')
        print('provenanceRequired='+str(report['provenanceRequired'])+' eligible='+str(not a.test_only)+' reportSha256='+digest(payload))

if __name__=='__main__':
    main()
