# 認証済み固定main native binaryへの橋渡し。trusted-consumer.pyが選択tupleをstdinへ渡す。
# 180秒/出力8192bytesで制限し、proofのselection/policy/真偽値を独立照合する。
# rollbackは古いsourceを許すだけで、現在policyのscan/inspection検証を免除しない。
"""事前に由来を認証した固定mainのnative scan検証器だけを起動する。"""
import json
import re
import subprocess

def inspect(binary, selection, source_main, rollback=False):
    if (not re.fullmatch('[0-9a-f]{40}', source_main)
            or (not rollback and selection.get('sourceCommit') != source_main)):
        raise ValueError('current source main required for new adoption')
    data=json.dumps(selection,sort_keys=True,separators=(',',':')).encode()
    if len(data)>8192:
        raise ValueError('selection exceeds fixed native profile')
    mode='rollback-inspected' if rollback else 'current-inspected'
    try:
        run=subprocess.run([str(binary),mode],input=data,stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,timeout=180)
    except (OSError,subprocess.TimeoutExpired):
        raise ValueError('fixed native scan inspection refused') from None
    if run.returncode or len(run.stdout)>8192:
        raise ValueError('fixed native scan inspection refused')
    try:
        proof=json.loads(run.stdout)
    except (ValueError,UnicodeError):
        raise ValueError('fixed native proof malformed') from None
    expected={'selection','policyRevision','scanBytesVerified','nativeRecordMatched',
              'nativeInspectionMatched','adoptionAuthorized'}
    if (not isinstance(proof,dict) or set(proof)!=expected
            or proof['selection']!=selection or proof['policyRevision']!=source_main
            or any(proof[k] is not True for k in ('scanBytesVerified','nativeRecordMatched','nativeInspectionMatched'))
            or proof['adoptionAuthorized'] is not False):
        raise ValueError('current native scan, writer and inspection binding differs')
    return proof
