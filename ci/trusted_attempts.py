# R01/R02の最新attempt・時間有効性ゲート。trusted-consumer.pyとpre_merge_check.pyが共用する。
# 固定safe writer JSONとGitLab実job一覧を読取り、選択runの期限または拒否例外を返す。
# raw trace/OCI/pipeline変数を読まない。docs/review-r01-r02.mdとtrusted_attempts_test.pyを参照。
"""保護manifestゲートの読取専用検証。後続attemptと時間失効をlive照合する。
後続証跡が欠落/曖昧なら古い成功を復活させず拒否する。
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

SCAN_NAMES={'image-scan','oci-image-policy-validation','oci-image-rescan-validation'}
WRITER_NAMES={'release-record-store','release-record-store-rescan'}

def utc(value):
    if not isinstance(value,str):
        raise ValueError('UTC timestamp required')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset()!=timedelta(0):
        raise ValueError('UTC timestamp required')
    return parsed

def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('duplicate safe record key')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid number')))

def job_identity(job,project,pipeline,policy):
    return (type(job.get('id')) is int and job['id']>0 and job.get('ref')=='main'
            and job.get('allow_failure') is False
            and job.get('commit',{}).get('id')==policy
            and job.get('pipeline',{}).get('id')==pipeline
            and job.get('pipeline',{}).get('project_id')==project
            and job.get('pipeline',{}).get('sha')==policy)

# retry jobも一覧へ含める。100件に達すると一覧の完全性を保証できないため成功にしない。
def jobs(api,project,pipeline):
    result=api.json(f'/projects/{project}/pipelines/{pipeline}/jobs?include_retried=true&per_page=100')
    if not isinstance(result,list) or len(result)>=100:
        raise ValueError('attempt job inventory incomplete')
    return result

# 成功した保護main writerのstored.jsonと実record bytesをchecksumで結合する。
# writerが成功でもrecord.decisionがfailedなら、その失敗を古い成功で打ち消さない。
def exported_record(api,project,pipeline,scan,policy,inventory):
    matched=[]
    for writer in inventory:
        if writer.get('name') not in WRITER_NAMES:
            continue
        if not job_identity(writer,project,pipeline,policy) or writer.get('status')!='success':
            continue
        base=f'/projects/{project}/jobs/{writer["id"]}/artifacts/.release-store/public/'
        proof=strict_json(api.read(base+'stored.json',8192))
        run=proof.get('run',{})
        if run.get('PipelineID')!=pipeline or run.get('JobID')!=scan:
            continue
        raw=api.read(base+'record.json',1024**2)
        record=strict_json(raw)
        service='frontend' if project==86247025 else 'backend'
        digest=record.get('imageDigest','')
        if not re.fullmatch('sha256:[0-9a-f]{64}',digest): raise ValueError('invalid attempt digest')
        record_url=(f'https://gitlab.com/api/v4/projects/{project}/packages/generic/core-platform-{service}/'
                    f'sha256-{digest[7:]}/release-record-{pipeline}-{scan}.json')
        expected_run={'Service':record.get('service'),'Digest':record.get('imageDigest'),
                      'PipelineID':pipeline,'JobID':scan}
        if (record.get('schemaVersion')!=2 or record.get('sourceProjectId')!=project or record.get('service')!=service
                or record.get('policyRevision')!=policy or record.get('scanPipelineId')!=pipeline
                or record.get('scanJobId')!=scan or run!=expected_run
                or proof.get('schemaVersion')!=1 or proof.get('decision')!=record.get('decision')
                or proof.get('recordUrl')!=record_url
                or proof.get('recordSha256')!=hashlib.sha256(raw).hexdigest()
                or any(proof.get(k) is not True for k in ('completedJobAuthority','protectedMainWriter',
                        'recordWrittenLast','serverDuplicateDenied'))):
            raise ValueError('later attempt writer binding refused')
        matched.append((record,hashlib.sha256(raw).hexdigest()))
    if len(matched)!=1:
        raise ValueError('one immutable attempt record required')
    return matched[0]

# scan+24h、DB更新+24h、各例外期限の最短をvalidUntilにする。期限到達時点で拒否。
def validity(record,checksum,now):
    scanned=utc(record.get('scannedAt')); database=utc(record.get('database',{}).get('updatedAt'))
    if record.get('decision')!='passed' or scanned>now or database>scanned:
        raise ValueError('successful current scan required')
    exceptions=record.get('exceptions')
    if not isinstance(exceptions,list): raise ValueError('exception inventory required')
    expiry=[e['expiresAt'] for e in exceptions]
    until=min([scanned+timedelta(hours=24),database+timedelta(hours=24)]+[utc(e) for e in expiry])
    if now>=until: raise ValueError('scan, database or exception expired; reverify')
    return {'recordSha256':checksum,'scannedAt':record['scannedAt'],
            'dbUpdatedAt':record['database']['updatedAt'],'exceptionExpiresAt':expiry,
            'validUntil':until.isoformat()}

# job IDだけでなく開始時刻も比べ、古いpipelineで後から実行されたmanual/retryも後続attemptとして検出する。
# 未実行manualはattemptでない。後続証跡不足/実行中/取得失敗は安全側へ拒否し、古い成功へfallbackしない。
def inspect(api,selection,policy,now=None):
    now=now or datetime.now(timezone.utc)
    project=selection['sourceProjectId']; pipeline=selection['scanPipelineId']; scan=selection['scanJobId']
    if (project not in (86247025,86247033) or any(type(x) is not int or x<=0 for x in (pipeline,scan))
            or not re.fullmatch('[0-9a-f]{40}',policy)):
        raise ValueError('fixed attempt identity required')
    selected=api.json(f'/projects/{project}/jobs/{scan}')
    if (not job_identity(selected,project,pipeline,policy) or selected.get('name') not in SCAN_NAMES
            or selected.get('status')!='success'):
        raise ValueError('successful selected attempt required')
    started=utc(selected.get('started_at'))
    if started>now: raise ValueError('attempt start is in the future')
    inventory=jobs(api,project,pipeline)
    if sum(j.get('id')==scan for j in inventory)!=1: raise ValueError('selected attempt inventory differs')
    record,checksum=exported_record(api,project,pipeline,scan,policy,inventory)
    for key in ('service','sourceProjectId','sourceCommit','imageDigest','buildPipelineId','buildJobId',
                'scanPipelineId','scanJobId'):
        if record.get(key)!=selection.get(key): raise ValueError('selected record binding differs')
    if checksum!=selection.get('recordSha256'): raise ValueError('selected record checksum differs')
    seen=set(); selected_seen=False
    # 同policyの保護main pipelineを最大1000件まで列挙。上限到達や不完全一覧は拒否する。
    # 同じdigestの後続runが合格でも、その新runを明示選択して再検証する必要がある。
    for page in range(1,11):
        pipes=api.json(f'/projects/{project}/pipelines?sha={policy}&ref=main&order_by=id&sort=desc&per_page=100&page={page}')
        if not isinstance(pipes,list) or len(pipes)>100: raise ValueError('attempt inventory malformed')
        for pipe in pipes:
            pid=pipe.get('id')
            if (type(pid) is not int or pid<=0 or pid in seen or pipe.get('sha')!=policy
                    or pipe.get('ref')!='main'):
                raise ValueError('attempt pipeline identity differs')
            seen.add(pid); selected_seen=selected_seen or pid==pipeline
            items=inventory if pid==pipeline else jobs(api,project,pid)
            for job in items:
                if job.get('name') not in SCAN_NAMES or job.get('id')==scan:
                    continue
                if not job_identity(job,project,pid,policy): raise ValueError('attempt job identity differs')
                # まだ実行していないmanual jobは再scan attemptとして数えない。
                if job.get('status')=='manual' and job.get('started_at') is None: continue
                if job.get('started_at') is None:
                    if pid>=pipeline: raise ValueError('new attempt unresolved; reverify')
                    continue
                if (utc(job['started_at']),job['id']) <= (started,scan): continue
                later,_=exported_record(api,project,pid,job['id'],policy,items)
                if later.get('scanJobStatus')!=job.get('status'):
                    raise ValueError('attempt terminal status differs')
                # 同policyの同じ不変imageでは、実行済みの最新attemptだけを選択できる。後続runも成功した場合でも古い選択を許さない。
                if later.get('imageDigest')==record['imageDigest']:
                    raise ValueError('selected attempt superseded; old success cannot be revived')
        if len(pipes)<100: break
    else: raise ValueError('attempt inventory exceeds bound')
    if not selected_seen: raise ValueError('selected pipeline absent from live inventory')
    return validity(record,checksum,now)

# pre-mergeでも現在UTCで期限を再計算する。reportの自己申告validUntilをそのまま信用せず、期限延長や未来時刻も拒否。
def validate_report(report,now=None):
    now=now or datetime.now(timezone.utc)
    verified=utc(report.get('verifiedAt'))
    if verified>now or now>=verified+timedelta(hours=24): raise ValueError('verification report expired')
    images=report.get('verifiedImages')
    if not isinstance(images,list): raise ValueError('verified image inventory required')
    deadlines=[verified+timedelta(hours=24)]
    for image in images:
        v=image.get('validity',{}); selection=image.get('phase2Selection',{})
        if v.get('recordSha256')!=selection.get('recordSha256'): raise ValueError('expiry record binding differs')
        scanned=utc(v.get('scannedAt')); database=utc(v.get('dbUpdatedAt'))
        expiry=v.get('exceptionExpiresAt')
        if not isinstance(expiry,list) or database>scanned or scanned>verified:
            raise ValueError('invalid scan expiry inputs')
        until=min([scanned+timedelta(hours=24),database+timedelta(hours=24)]+[utc(e) for e in expiry])
        if utc(v.get('validUntil'))!=until or now>=until: raise ValueError('selected evidence expired; reverify')
        deadlines.append(until)
    if utc(report.get('validUntil'))!=min(deadlines): raise ValueError('aggregate expiry differs')

# 全選択imageの期限とreport作成+24hの最短を全体期限として返す。
def report_deadline(images,verified):
    return min([verified+timedelta(hours=24)]+[utc(x['validity']['validUntil']) for x in images]).isoformat()
