# render inventoryとrelease注釈のデータ契約。trusted-consumer.pyから呼び、変更/削除imageと選択tupleを分類する。
# Pod以外のJob/CronJob/initContainer等も対象にし、許可されないimageや未検査legacy候補を拒否する。
"""Phase 0のartifact/image方針をデータとして検証する。MRのscriptはimportしない。"""
import hashlib
import json
import re

APPS={
    'registry.gitlab.com/syuhei-platform-engineering-lab/frontend-app':('frontend',86247025),
    'registry.gitlab.com/syuhei-platform-engineering-lab/backend-app':('backend',86247033),
}
POSTGRES=('docker.io/library/postgres','17.10-bookworm','sha256:9b18b78397054fce88a9552e9d5a3ad5bb7fd258c5b3cc1c5028e46373d6ea8f')
FIELDS=('namespace','kind','name','podSpecPath','containerType','containerName','repository','tag','digest','release')
KINDS={'Deployment','StatefulSet','DaemonSet','ReplicaSet','ReplicationController','Job','CronJob','Pod'}

def sha(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{40}',value):
        raise ValueError('full commit SHA required')
    return value

def decimal(value):
    if not isinstance(value,str) or not re.fullmatch('[1-9][0-9]*',value):
        raise ValueError('positive numeric identifier required')
    return int(value)

def classify(entry):
    if set(entry)!=set(FIELDS) or any(not isinstance(entry[k],str) for k in FIELDS[:-1]):
        raise ValueError('invalid inventory entry schema')
    if entry['kind'] not in KINDS or entry['containerType'] not in ('containers','initContainers','ephemeralContainers'):
        raise ValueError('unknown PodSpec inventory identity')
    if not re.fullmatch('sha256:[0-9a-f]{64}',entry['digest']):
        raise ValueError('fixed image digest required')
    release=entry['release']
    if not isinstance(release,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in release.items()):
        raise ValueError('invalid release schema')
    repository=entry['repository']
    if repository not in APPS:
        if (repository,entry['tag'],entry['digest'])!=POSTGRES or release:
            raise ValueError('image is outside the reviewed repository/version/digest allowlist')
        return None
    service,project=APPS[repository]
    if release.get('account.lab/release-schema-version')=='2':
        fields=('source-project-id','source-commit','build-pipeline-id','build-job-id',
                'scan-pipeline-id','scan-job-id','release-record-url','release-record-sha256','sbom-url','sbom-sha256')
        required={'account.lab/'+field for field in fields}|{'account.lab/image-digest','account.lab/release-schema-version'}
        if set(release)!=required or decimal(release['account.lab/source-project-id'])!=project:
            raise ValueError('Phase 2 release annotation schema or project mismatch')
        commit=sha(release['account.lab/source-commit'])
        identifiers={field:decimal(release['account.lab/'+field]) for field in ('build-pipeline-id','build-job-id','scan-pipeline-id','scan-job-id')}
        if any(value>9223372036854775807 for value in identifiers.values()):
            raise ValueError('Phase 2 identifier exceeds signed integer range')
        if entry['tag']!=commit or release['account.lab/image-digest']!=entry['digest']:
            raise ValueError('Phase 2 image differs from selected release')
        root=('https://gitlab.com/api/v4/projects/'+str(project)+'/packages/generic/core-platform-'+service+
              '/sha256-'+entry['digest'][7:]+'/')
        run=str(identifiers['scan-pipeline-id'])+'-'+str(identifiers['scan-job-id'])
        if (release['account.lab/release-record-url']!=root+'release-record-'+run+'.json'
                or release['account.lab/sbom-url']!=root+'sbom-'+run+'.cdx.json'
                or any(not re.fullmatch('[0-9a-f]{64}',release['account.lab/'+field]) for field in ('release-record-sha256','sbom-sha256'))):
            raise ValueError('Phase 2 run URL or external checksum mismatch')
        selection={'service':service,'sourceProjectId':project,'sourceCommit':commit,'imageDigest':entry['digest'],
            'buildPipelineId':identifiers['build-pipeline-id'],'buildJobId':identifiers['build-job-id'],
            'scanPipelineId':identifiers['scan-pipeline-id'],'scanJobId':identifiers['scan-job-id'],
            'recordUrl':release['account.lab/release-record-url'],'recordSha256':release['account.lab/release-record-sha256'],
            'sbomUrl':release['account.lab/sbom-url'],'sbomSha256':release['account.lab/sbom-sha256']}
        return {'service':service,'projectId':project,'commit':commit,'repository':repository,
                'digest':entry['digest'],'phase2Selection':selection}
    required={'account.lab/image-digest','account.lab/source-project-id','account.lab/source-commit','account.lab/pipeline-id','account.lab/pipeline-url'}
    if set(release)!=required or decimal(release['account.lab/source-project-id'])!=project:
        raise ValueError('repository/project or release annotation mismatch')
    commit=sha(release['account.lab/source-commit'])
    pipeline=decimal(release['account.lab/pipeline-id'])
    if entry['tag']!=commit or release['account.lab/image-digest']!=entry['digest']:
        raise ValueError('tag/digest differs from selected release')
    if release['account.lab/pipeline-url']!='https://gitlab.com/syuhei-platform-engineering-lab/'+service+'-app/-/pipelines/'+str(pipeline):
        raise ValueError('release URL differs from fixed project and numeric pipeline')
    return {'service':service,'projectId':project,'commit':commit,'pipelineId':pipeline,'repository':repository,'digest':entry['digest']}

def require_inspected_candidate(entries):
    """候補Applicationがscan導入前のreleaseへ戻ることを拒否する。"""
    for entry in entries:
        proof=classify(entry)
        if proof is not None and 'phase2Selection' not in proof:
            raise ValueError('candidate application requires an inspected Phase 2 release')

def identity(entry):
    return tuple(entry[k] for k in FIELDS[:6])

def inventory(value):
    if set(value)!={'schema','renderSha256','inventorySha256','entries'} or value['schema']!='core-platform/image-inventory/v1':
        raise ValueError('inventory schema mismatch')
    entries=value['entries']
    if not isinstance(entries,list) or not 0<len(entries)<=1000:
        raise ValueError('invalid inventory size')
    seen=set()
    for entry in entries:
        classify(entry)
        key=identity(entry)
        if key in seen:
            raise ValueError('duplicate PodSpec container identity')
        seen.add(key)
    for field in ('renderSha256','inventorySha256'):
        if not re.fullmatch('[0-9a-f]{64}',str(value[field])):
            raise ValueError('inventory checksum format mismatch')
    # encoding/jsonのstruct field順、map key sort、HTML escapeへ合わせる。
    canonical=[{key:dict(sorted(entry[key].items())) if key=='release' else entry[key] for key in FIELDS} for entry in entries]
    encoded=json.dumps(canonical,ensure_ascii=False,separators=(',',':'))
    for literal,escaped in (('<','\\u003c'),('>','\\u003e'),('&','\\u0026'),('\u2028','\\u2028'),('\u2029','\\u2029')):
        encoded=encoded.replace(literal,escaped)
    if hashlib.sha256(encoded.encode()).hexdigest()!=value['inventorySha256']:
        raise ValueError('inventory checksum differs from canonical entries')
    return {identity(entry):entry for entry in entries}

def binding(payload,checksum,job,mr,project,pipeline,verifier,producer_id):
    if len(payload)>8*1024**2 or hashlib.sha256(payload).hexdigest()!=checksum.strip():
        raise ValueError('producing artifact checksum mismatch')
    report=json.loads(payload)
    sha(verifier)
    if (job.get('id')!=producer_id or job.get('name')!='trusted-render-producer'
            or job.get('status')!='success' or job.get('ref')!='main'
            or job.get('commit',{}).get('id')!=verifier
            or job.get('pipeline',{}).get('id')!=pipeline
            or job.get('pipeline',{}).get('sha')!=verifier):
        raise ValueError('producer is not the successful fixed protected-main pipeline job')
    expected={'projectId':project,'pipelineId':pipeline,'jobId':producer_id,'jobName':'trusted-render-producer',
        'ref':'main','commitSha':verifier,'mrIid':mr.get('iid')}
    if (report.get('schema')!='core-platform/render-binding/v1'
            or report.get('eligibleForAuthenticatedVerification') is not True
            or report.get('serverIdentity')!=expected
            or report.get('verifierSha')!=verifier or report.get('targetSha')!=verifier
            or report.get('producerJobId')!=str(producer_id)
            or report.get('sourceSha')!=mr.get('sha')
            or mr.get('state')!='opened' or mr.get('target_branch')!='main'
            or mr.get('source_project_id')!=project or mr.get('target_project_id')!=project):
        raise ValueError('artifact identity, target/source/verifier SHA or MR state mismatch')
    old=inventory(report['renders']['before']['inventory'])
    new=inventory(report['renders']['candidate']['inventory'])
    changed=[entry for key,entry in new.items() if old.get(key)!=entry]
    removed=[entry for key,entry in old.items() if key not in new]
    if report.get('provenanceRequired')!=(old!=new):
        raise ValueError('claimed change decision differs from inventory')
    return report,changed,removed

def source_pipeline(pipeline,proof,main_sha,rollback=False):
    if (pipeline.get('id')!=proof['pipelineId'] or pipeline.get('status')!='success'
            or pipeline.get('ref')!='main' or pipeline.get('sha')!=proof['commit']):
        raise ValueError('source pipeline is not a successful matching main build')
    if not rollback and main_sha!=proof['commit']:
        raise ValueError('source main advanced; regenerate the proposal')
