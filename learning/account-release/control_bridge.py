"""Fixed JSON bridge for the authenticated Backstage backend; not a shell API."""
import argparse
import datetime
import json
from pathlib import Path
import sys

from contract import Rejected, require
from control_adapter import LiveAdapter
from operations import Coordinator


def execute(settings,envelope):
    require(set(settings)=={'database','catalog','allowed_actors','adapter'},'invalid-bridge-settings')
    require(set(envelope)=={'operation','actor','payload'},'invalid-envelope')
    catalog=json.loads(Path(settings['catalog']).read_text())
    adapter=LiveAdapter(settings['adapter'],catalog)
    core=Coordinator(settings['database'],catalog,adapter,settings['allowed_actors'])
    actor,payload,op=envelope['actor'],envelope['payload'],envelope['operation']
    try:
        core.authorize(actor)
        if op=='state':
            require(payload=={},'invalid-state-request')
            current=adapter.observe()
            return {'current':current,'catalog':[{'image':e['image'],'source_commit':e['source_commit']} for e in catalog],
                    'history':core.history(actor),'fetched_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'verdict':adapter.verdict(current),'analysis_history':adapter.verdict_history(current)}
        if op=='history':
            require(payload=={},'invalid-history-request');return core.history(actor)
        if op in ('preview','request'):
            require(set(payload)=={'kind','payload'},'invalid-request-envelope')
            return (core.preview(actor,payload['payload'],payload['kind']) if op=='preview' else
                    core.submit(actor,payload['payload'],payload['kind']))
        if op=='action':return core.submit(actor,payload,'action')
        if op in ('reconcile','apply_reviewed'):
            require(set(payload)=={'request_id'},'invalid-reconcile-fields')
            return getattr(core,op)(actor,payload['request_id'])
        if op=='advance':
            require(set(payload)=={'request_id','expected'},'invalid-advance-fields')
            return core.advance(actor,payload['request_id'],payload['expected'])
        raise Rejected('unsupported-operation')
    finally:core.db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);args=parser.parse_args()
    try:
        raw=sys.stdin.read(16385);require(len(raw)<=16384,'request-too-large')
        envelope=json.loads(raw);settings=json.loads(args.config.read_text())
        value=execute(settings,envelope);result={'ok':True,'value':value}
    except Rejected as exc:
        # Rejected messages are fixed internal reason codes, never raw IO output.
        result={'ok':False,'code':str(exc) if str(exc).replace('-','').isalnum() else 'operation-rejected'}
    except Exception:
        result={'ok':False,'code':'operation-outcome-unknown'}
    print(json.dumps(result,allow_nan=False))
