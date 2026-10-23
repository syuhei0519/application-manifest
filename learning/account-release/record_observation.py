"""Operator-only ingestion of measured receipts. Not exposed by Backstage HTTP.

Capture fixed-target before/after objects and the measured 200-call Envoy/client
window first. This command repeats live identity and pure receipt verification;
it never fabricates traffic, issues credentials, changes Git or patches Kubernetes.
"""
import argparse
import json
from pathlib import Path

from contract import require
from control_adapter import LiveAdapter
from evidence_verifier import verify
from operations import Coordinator
from verified_state import update


def record(settings,receipt,request_id=None):
    catalog=json.loads(Path(settings['catalog']).read_text())
    adapter=LiveAdapter(settings['adapter'],catalog)
    current=adapter.observe();phase=None
    if request_id is not None:
        core=Coordinator(settings['database'],catalog,adapter,settings['allowed_actors'])
        try:
            row=core.record(request_id)
            require(row is not None and row['kind'] in ('release','restore'),'observation-request-not-found')
            revision,phase=adapter.verified_merge(adapter.reviewed(core.proposal_id(row),row['expected']),row['expected'])
            require(current['git_revision']==revision and current['rollout_uid']==row['expected']['identity']['rollout_uid'],
                    'observation-request-identity-mismatch')
        finally:core.db.close()
    result=verify(receipt,current,catalog,adapter.clock(),phase=phase)
    proof={k:current[k] for k in ('rollout_uid','generation','git_revision','requested_image','revision')}
    proof.update(result,receipt=receipt)
    if phase is not None:proof['phase']=phase['name']
    def save(state):
        state['stable_recovery' if receipt['kind']=='recovery' else 'completion']=proof
        if receipt['kind']=='completion':
            state['verified_current']={'current':current,'proof':proof}
    update(settings['adapter']['state_file'],save)
    return {k:result[k] for k in ('verified_at','receipt_sha256','traffic_health_verified','resync_restart_verified')}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True);p.add_argument('--request-id')
    args=p.parse_args()
    print(json.dumps(record(json.loads(args.config.read_text()),json.loads(args.receipt.read_text()),args.request_id)))
