# native検証binaryのjob/固定main/SHAとbytes checksumを照合する境界。
# ファイル名やMR提供binaryだけで実行を許可せず、同じ保護pipelineのsecurity-policy-test由来を確認する。
"""安全なnative検証binaryのartifactを、固定された保護mainのjobへ結合する。"""
import hashlib
import re

def verify(job, proof, binary, project, pipeline, verifier):
    if (type(project) is not int or type(pipeline) is not int
            or not re.fullmatch('[0-9a-f]{40}', verifier)
            or job.get('name') != 'security-policy-test'
            or job.get('status') != 'success' or job.get('allow_failure') is not False
            or job.get('ref') != 'main' or job.get('commit', {}).get('id') != verifier
            or job.get('pipeline', {}).get('id') != pipeline
            or job.get('pipeline', {}).get('project_id') != project
            or job.get('pipeline', {}).get('sha') != verifier):
        raise ValueError('fixed native verifier job required')
    expected = {'schemaVersion': 1, 'projectId': project, 'pipelineId': pipeline,
                'jobId': job.get('id'), 'sourceCommit': verifier,
                'binarySha256': hashlib.sha256(binary).hexdigest()}
    if proof != expected or type(job.get('id')) is not int or job['id'] <= 0 or not binary:
        raise ValueError('fixed native verifier binary binding differs')
    return expected
