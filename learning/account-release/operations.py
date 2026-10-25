# リリース操作の受付・予約・結果保存を調整し、結果が不明な操作を安易に繰り返さないようにします。
"""Account release coordinator. Durable reservations survive retries and restarts.

Adapters perform Git/Rollouts IO. A failed or timed-out call is never implicitly
repeated: reconciliation is read-only until its outcome is known. No credentials
or arbitrary adapter exceptions are written to the audit store.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import re
import sqlite3
import time

from contract import DefinitiveNoWriteRejected, Rejected, require, validate_catalog
from release_modes import ENTITY, ENVIRONMENT, validate_request, switch_plan

ID = re.compile(r'[A-Za-z0-9_-]{8,80}')
SHA = re.compile(r'[a-f0-9]{40}')
ACTOR = re.compile(r'user:default/[a-z0-9][a-z0-9._-]{0,62}')
PUBLIC_REASONS = {'review-pending','git-not-merged','desired-not-synced','runtime-progressing',
                  'stable-recovery-pending','sync-failed','source-unavailable','permission-denied',
                  'operation-not-observed','identity-changed','phase-verified','runtime-verified',
                  'durable-restore-verified','proposal-not-found','proposal-closed'}


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Coordinator:
    def __init__(self, path, catalog, adapter, allowed_actors, clock=time.time):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
            actor TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
            expected TEXT NOT NULL, result TEXT, created REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS exclusion (
            target TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id));
          CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
            actor TEXT NOT NULL, source TEXT NOT NULL, type TEXT NOT NULL,
            at REAL NOT NULL, detail TEXT NOT NULL);
        ''')
        self.catalog, self.adapter, self.actors, self.clock = catalog, adapter, set(allowed_actors), clock
        validate_catalog(catalog)

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def authorize(self, actor):
        require(isinstance(actor, str) and ACTOR.fullmatch(actor) and actor in self.actors,
                'actor-not-authorized')

    def event(self, request_id, actor, kind, detail, source='account-coordinator'):
        self.db.execute('INSERT INTO events(request_id,actor,source,type,at,detail) VALUES(?,?,?,?,?,?)',
                        (request_id, actor, source, kind, self.clock(), encode(detail)))

    def record(self, request_id):
        row = self.db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ('payload', 'expected', 'result'):
            result[key] = json.loads(result[key]) if result[key] else None
        result.pop('fingerprint')
        return result

    def existing(self, request_id, fingerprint, actor):
        row = self.db.execute('SELECT fingerprint,actor FROM requests WHERE id=?', (request_id,)).fetchone()
        if row:
            require(row['fingerprint'] == fingerprint and row['actor'] == actor, 'request-id-reused')
            return self.record(request_id)
        return None

    def submit(self,actor,payload,kind='release'):
        """Record a known pre-dispatch rejection so a lost HTTP response is recoverable."""
        self.authorize(actor)
        request=payload.get('request',{}) if kind=='restore' else payload
        request_id=request.get('request_id')
        require(isinstance(request_id,str) and ID.fullmatch(request_id),'invalid-request-id')
        require(kind in ('release','restore','action'),'invalid-operation-kind')
        fingerprint=hashlib.sha256(encode(payload if kind=='action' else {'kind':kind,'payload':payload}).encode()).hexdigest()
        try:
            return self.action(actor,payload) if kind=='action' else self.request(actor,payload,kind)
        except Rejected as exc:
            with self.transaction():
                # Every external effect is preceded by a committed request row.
                # Absence therefore proves this dispatch had no external effect.
                require(self.record(request_id) is None,'existing-request-rejected')
                code=str(exc)
                reason=code if re.fullmatch('[a-z0-9-]{1,100}',code) else 'request-rejected'
                result={'state':'rejected','reason':reason,'git_change_absent':True}
                self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?)',
                    (request_id,encode(payload),fingerprint,actor,kind,'rejected',encode({}),encode(result),self.clock()))
                self.event(request_id,actor,'rejected',result)
            return self.record(request_id)

    @staticmethod
    def identity(snapshot):
        keys = ('release_id', 'git_revision', 'generation', 'version', 'rollout_uid',
                'resource_version', 'current_step', 'requested_image')
        return {key: snapshot[key] for key in keys}

    def preview(self, actor, payload, kind='release'):
        self.authorize(actor)
        current = self.adapter.observe()
        if kind == 'restore':
            require(set(payload) == {'request', 'reason', 'previous_git_revision'}, 'invalid-restore-fields')
            require(isinstance(payload['reason'], str) and 3 <= len(payload['reason']) <= 300,
                    'restore-reason-required')
            require(not any(x in payload['reason'].lower() for x in ('token=', 'password=', 'secret=')),
                    'sensitive-reason-rejected')
            previous = current.get('verified_previous')
            require(isinstance(previous, dict) and previous.get('verified') is True,
                    'verified-previous-required')
            require(payload['previous_git_revision'] == previous.get('git_revision') and
                    isinstance(previous.get('git_revision'), str) and SHA.fullmatch(previous['git_revision']),
                    'previous-revision-mismatch')
            request = payload['request']
            require(request['image'] == previous.get('image'), 'previous-image-mismatch')
            require(current['state'] in ('succeeded', 'restored', 'recovering') and
                    current.get('stable_traffic_verified') is True, 'abort-and-stable-recovery-required')
            # Stable recovery may intentionally differ from Git's aborted candidate.
            eligible = dict(current, state='succeeded', git_runtime_drift=False,
                            current_image=current['requested_image'])
            validate_request(request, self.catalog, eligible)
            require((request['strategy'], request['promotion_mode']) ==
                    (current['strategy'], current['promotion_mode']), 'restore-cannot-change-mode')
            phases = [dict(name='restore-image', image=previous['image'], strategy=current['strategy'],
                           promotion_mode=current['promotion_mode'], bootstrap_hash='',
                           verify='git-sync-runtime-traffic-health-and-restart')]
        else:
            require(kind == 'release', 'invalid-operation-kind')
            request = payload
            phases = switch_plan(request, self.catalog, current)
        return {'request': deepcopy(request), 'current': current, 'phases': phases,
                'diff': self.adapter.diff(current['git_revision'], phases[0]),
                'requires_review': True, 'kind': kind}

    def request(self, actor, payload, kind='release'):
        self.authorize(actor)
        request = payload.get('request', {}) if kind == 'restore' else payload
        request_id = request.get('request_id')
        require(isinstance(request_id, str) and ID.fullmatch(request_id), 'invalid-request-id')
        fingerprint = hashlib.sha256(encode({'kind': kind, 'payload': payload}).encode()).hexdigest()
        with self.transaction():
            existing = self.existing(request_id, fingerprint, actor)
            if existing:
                return existing
            preview = self.preview(actor, payload, kind)
            # Every deterministic external identity must be valid before the
            # durable target reservation is created.  A valid request id can
            # still be too long after the phase suffix is added.
            for phase in range(len(preview['phases'])):
                require(ID.fullmatch(self.phase_proposal_id(request_id, phase)) is not None,
                        'derived-proposal-id-invalid')
            lock = self.db.execute('SELECT request_id FROM exclusion WHERE target=?', (ENTITY,)).fetchone()
            if lock:
                # Abort is deliberately non-terminal. A verified recovery may
                # transfer its reservation to a restore, never simply unlock it.
                current = preview['current']
                require(kind == 'restore' and current['state'] == 'recovering' and
                        current.get('release_request_id') == lock['request_id'] and
                        current.get('stable_traffic_verified') is True, 'service-environment-busy')
                prior = self.record(lock['request_id'])
                require(prior['kind'] in ('release', 'restore') and prior['state'] in ('running','recovering','sync-failed'),
                        'restore-reservation-mismatch')
                self.db.execute('DELETE FROM exclusion WHERE target=?', (ENTITY,))
                self.db.execute('UPDATE requests SET state=? WHERE id=?', ('recovery-superseded', prior['id']))
                self.event(prior['id'], actor, 'reservation-transferred-to-restore', {'restore_request_id': request_id})
            expected = {'identity': self.identity(preview['current']), 'phases': preview['phases'], 'phase': 0}
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?)',
                            (request_id, encode(payload), fingerprint, actor, kind, 'accepted', encode(expected),
                             None, self.clock()))
            self.db.execute('INSERT INTO exclusion VALUES(?,?)', (ENTITY, request_id))
            self.event(request_id, actor, 'accepted', {'kind': kind, 'image': request['image'],
                       'git_revision': request['expected_git_revision'],'strategy':request['strategy'],
                       'promotion_mode':request['promotion_mode']})
        # Reservation commits before external IO. A process crash cannot lose it.
        return self._propose(request_id)

    def _propose(self, request_id):
        with self.transaction():
            row = self.record(request_id)
            require(row['state'] == 'accepted', 'proposal-not-ready')
            self.require_owner(request_id)
            self.db.execute('UPDATE requests SET state=? WHERE id=?', ('dispatching',request_id))
        # A dispatched proposal holds the SQLite writer fence until its response
        # is recorded. Crash rollback leaves durable dispatching, resolved only
        # by a read; restore cannot take a dispatching reservation.
        with self.transaction():
            current = self.record(request_id)
            self.require_owner(request_id)
            require(current['state']=='dispatching' and current['expected']==row['expected'],'proposal-fence-changed')
            try:
                phase = row['expected']['phase']
                result = self.adapter.propose(self.proposal_id(row), row['expected']['identity']['git_revision'],
                                              row['expected']['phases'][phase])
                self._validate_proposal(result)
                require(result['base_revision']==row['expected']['identity']['git_revision'],
                        'proposal-base-mismatch')
            except DefinitiveNoWriteRejected as exc:
                # Only this explicit adapter type may close the request and
                # release its exclusion after dispatch started.  It is raised
                # solely before the adapter's first mutating call.
                reason=str(exc)
                require(re.fullmatch('[a-z0-9-]{1,100}',reason) is not None,
                        'invalid-no-write-rejection')
                result={'state':'rejected','reason':reason,'git_change_absent':True}
                self.db.execute('UPDATE requests SET state=?,result=? WHERE id=?',
                                ('rejected',encode(result),request_id))
                self.db.execute('DELETE FROM exclusion WHERE target=? AND request_id=?',
                                (ENTITY,request_id))
                self.event(request_id,row['actor'],'rejected',result,'git-pre-write')
            except Exception:
                self.db.execute('UPDATE requests SET state=? WHERE id=?', ('outcome-unknown', request_id))
                self.event(request_id, row['actor'], 'proposal-outcome-unknown', {})
            else:
                self.db.execute('UPDATE requests SET state=?,result=? WHERE id=?',
                                ('review-pending', encode(result), request_id))
                self.event(request_id, row['actor'], 'review-pending', result, 'git')
        return self.record(request_id)

    def require_owner(self, request_id):
        owner=self.db.execute('SELECT request_id FROM exclusion WHERE target=?',(ENTITY,)).fetchone()
        require(owner and owner['request_id']==request_id,'reservation-no-longer-owned')

    @staticmethod
    def proposal_id(row):
        return Coordinator.phase_proposal_id(row['id'],row['expected']['phase'])

    @staticmethod
    def phase_proposal_id(request_id,phase):
        return request_id + '-phase-' + str(phase)

    def advance(self, actor, request_id, expected):
        """Plan the next independently reviewed Git phase after observed proof."""
        self.authorize(actor)
        with self.transaction():
            row = self.record(request_id)
            require(row and row['kind'] == 'release' and row['state'] == 'phase-review-required',
                    'phase-not-ready')
            self.require_owner(request_id)
            current = self.adapter.observe()
            require(expected == self.identity(current), 'stale-runtime-identity')
            proof = self.adapter.proposal_outcome(self.proposal_id(row), row['expected'], row['result'])
            require(proof.get('state') == 'phase-review-required' and proof.get('phase_verified') is True and
                    proof.get('phase_git_revision') == current['git_revision'], 'phase-evidence-incomplete')
            context = deepcopy(row['expected'])
            require(context['phase']+1 < len(context['phases']), 'no-next-phase')
            context['phase'] += 1
            context['identity'] = self.identity(current)
            self.db.execute('UPDATE requests SET state=?,expected=?,result=NULL WHERE id=?',
                            ('accepted', encode(context), request_id))
            self.event(request_id, actor, 'next-phase-accepted', {'phase': context['phase']})
        return self._propose(request_id)

    def apply_reviewed(self,actor,request_id):
        self.authorize(actor)
        with self.transaction():
            row=self.record(request_id)
            require(row and row['kind'] in ('release','restore') and row['state']=='apply-pending',
                    'apply-not-ready')
            self.require_owner(request_id)
            phase=row['expected']['phase']
            dispatched=self.db.execute("SELECT detail FROM events WHERE request_id=? AND type='apply-dispatched'",(request_id,)).fetchall()
            require(not any(json.loads(e['detail']).get('phase')==phase for e in dispatched),'apply-already-dispatched')
            try:
                # Preparation performs external reads and local bookkeeping;
                # it cannot patch Kubernetes. A missing/expired receipt therefore
                # does not consume the one permitted external dispatch.
                prepared=self.adapter.prepare_reviewed(self.proposal_id(row),row['expected'])
            except Exception:
                self.event(request_id,actor,'apply-preparation-incomplete',{'phase':phase})
                return self.record(request_id)
            # Commit uncertainty BEFORE IO; a crash cannot make this request
            # eligible for an implicit retry. Reconcile only reads the outcome.
            self.db.execute('UPDATE requests SET state=? WHERE id=?',('outcome-unknown',request_id))
            self.event(request_id,actor,'apply-dispatched',{'phase':phase})
        try:
            self.adapter.dispatch_reviewed(prepared)
        except Exception:
            with self.transaction():self.event(request_id,actor,'apply-outcome-unknown',{})
            return self.record(request_id)
        return self.reconcile(actor,request_id)

    @staticmethod
    def _validate_proposal(result):
        require(isinstance(result, dict) and set(result) == {'pr_url', 'commit', 'base_revision'},
                'invalid-git-proposal')
        require(re.fullmatch(r'https://github.com/syuhei0519/application-manifest/pull/[1-9][0-9]*', result['pr_url']),
                'unexpected-pr-target')
        require(SHA.fullmatch(result['commit']) and SHA.fullmatch(result['base_revision']), 'invalid-proposal-commit')

    def action(self, actor, payload):
        self.authorize(actor)
        fields = {'request_id', 'operation', 'entity', 'environment', 'expected'}
        require(isinstance(payload, dict) and set(payload) == fields, 'invalid-action-fields')
        require(payload['entity'] == ENTITY and payload['environment'] == ENVIRONMENT, 'target-out-of-scope')
        require(payload['operation'] in ('promote', 'abort'), 'unsupported-runtime-operation')
        request_id = payload['request_id']
        require(isinstance(request_id, str) and ID.fullmatch(request_id), 'invalid-request-id')
        fingerprint = hashlib.sha256(encode(payload).encode()).hexdigest()
        with self.transaction():
            existing = self.existing(request_id, fingerprint, actor)
            if existing:
                return existing
            # A release reservation remains held through its runtime. It is not a
            # second action lock. Pending/unknown actions block all further actions.
            require(not self.db.execute("SELECT id FROM requests WHERE kind='action' AND state IN ('accepted','outcome-unknown')").fetchone(),
                    'runtime-operation-unresolved')
            current = self.adapter.observe()
            require(payload['expected'] == self.identity(current), 'stale-runtime-identity')
            require(current['strategy'] == 'Canary' and payload['operation'] in current['actions'],
                    'operation-not-allowed-in-current-state')
            require(current.get('release_managed') is True and current.get('git_runtime_drift') is False,
                    'release-not-managed-or-drift')
            if payload['operation'] == 'promote':
                require(current['promotion_mode'] == 'manual' and current['state'] == 'paused' and
                        current.get('pause_reasons') == ['CanaryPauseStep'], 'manual-pause-required')
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?)',
                            (request_id, encode(payload), fingerprint, actor, 'action', 'accepted',
                             encode(payload['expected']), None, self.clock()))
            self.event(request_id, actor, 'action-accepted', {'operation': payload['operation'],
                       'release_id': current['release_id'], 'generation': current['generation']})
        try:
            self.adapter.act(payload['operation'], payload['expected'])
        except Exception:
            with self.transaction():
                self.db.execute('UPDATE requests SET state=? WHERE id=?', ('outcome-unknown', request_id))
                self.event(request_id, actor, 'action-outcome-unknown', {})
            return self.record(request_id)
        # Even an HTTP success is only an accepted mutation, not traffic recovery.
        return self.reconcile(actor, request_id)

    def reconcile(self, actor, request_id):
        self.authorize(actor)
        with self.transaction():
            row = self.record(request_id)
            require(row is not None, 'unknown-request')
            if row['state'] in ('succeeded','restored','rejected','recovery-superseded','confirmed','superseded'):
                return row
            if row['kind'] == 'action':
                evidence = self.adapter.action_outcome(row['payload']['operation'], row['expected'])
                require(evidence['state'] in ('confirmed', 'unknown', 'superseded'), 'invalid-action-observation')
                state = {'confirmed': 'confirmed', 'unknown': 'outcome-unknown', 'superseded': 'superseded'}[evidence['state']]
            else:
                self.require_owner(request_id)
                evidence = self.adapter.proposal_outcome(self.proposal_id(row), row['expected'], row['result'])
                state = evidence['state']
                require(state in ('outcome-unknown', 'review-pending', 'apply-pending', 'running',
                                  'recovering', 'sync-failed', 'rejected', 'succeeded', 'restored',
                                  'phase-review-required'), 'invalid-release-observation')
                if state in ('succeeded', 'restored'):
                    require(state == ('restored' if row['kind']=='restore' else 'succeeded'),
                            'wrong-terminal-kind')
                    require(evidence.get('git_runtime_verified') is True and
                            evidence.get('traffic_health_verified') is True and
                            row['expected']['phase'] == len(row['expected']['phases'])-1,
                            'terminal-evidence-incomplete')
                    if state == 'restored':
                        require(evidence.get('resync_restart_verified') is True, 'durable-restore-unverified')
                    self.db.execute('DELETE FROM exclusion WHERE request_id=?', (request_id,))
                if state == 'rejected':
                    require(evidence.get('git_change_absent') is True, 'rejection-effect-unknown')
                    self.db.execute('DELETE FROM exclusion WHERE request_id=?', (request_id,))
            # Store a deliberate public projection; adapter payloads may contain credentials.
            public = {'state':state}
            if 'reason' in evidence:
                reason=evidence['reason']
                public['reason']=reason if isinstance(reason,str) and reason in PUBLIC_REASONS else 'observation-unavailable'
            for key,pattern in [('commit',SHA),('pr_url',re.compile(r'https://github.com/syuhei0519/application-manifest/pull/[1-9][0-9]*')),
                                ('release_id',ID)]:
                if key in evidence:
                    require(isinstance(evidence[key],str) and pattern.fullmatch(evidence[key]),'unsafe-observation-identity')
                    public[key]=evidence[key]
            if 'generation' in evidence:
                require(type(evidence['generation']) is int and evidence['generation']>0,'unsafe-observation-generation')
                public['generation']=evidence['generation']
            if row['kind'] in ('release','restore') and 'release_id' in public:
                public['release_request_id']=request_id
            if 'source_time' in evidence:
                at=evidence['source_time']
                require(type(at) in (int,float) and math.isfinite(at) and 0<=at<=self.clock()+60,'invalid-source-time')
                public['source_time']=at
            if row['state'] != state or row['result'] != public:
                self.db.execute('UPDATE requests SET state=?,result=? WHERE id=?', (state, encode(public), request_id))
                self.event(request_id, actor, state, public, 'runtime' if row['kind'] == 'action' else 'git-runtime')
            return self.record(request_id)

    def history(self, actor):
        self.authorize(actor)
        events=[dict(dict(r),detail=json.loads(r['detail'])) for r in self.db.execute('SELECT * FROM events ORDER BY seq')]
        requests={r['id']:{'kind':r['kind'],'expected':json.loads(r['expected'])} for r in
                  self.db.execute('SELECT id,kind,expected FROM requests')}
        revisions={};actions={}
        for event in events:
            row=requests.get(event['request_id'],{});detail=event['detail']
            if row.get('kind') in ('release','restore'):
                detail['release_request_id']=event['request_id']
                commit=detail.get('commit')
                if event['source']=='git-runtime' and isinstance(commit,str) and SHA.fullmatch(commit):
                    revisions.setdefault(commit,set()).add(event['request_id'])
            elif row.get('kind')=='action':
                # Immutable stored action identity, never the current runtime.
                identity=row['expected']
                for key in ('release_id','generation'):
                    value=identity.get(key)
                    if (key=='release_id' and isinstance(value,str) and ID.fullmatch(value)) or (key=='generation' and type(value) is int and value>0):
                        detail[key]=value
                if event['request_id'] not in actions:
                    owners=revisions.get(identity.get('git_revision'),set())
                    actions[event['request_id']]=next(iter(owners)) if len(owners)==1 else None
                if actions[event['request_id']]:detail['release_request_id']=actions[event['request_id']]
        return events
