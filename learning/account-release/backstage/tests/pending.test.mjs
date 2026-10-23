import test from 'node:test';
import assert from 'node:assert/strict';
import { beginPending, readPending, settlePending, uncertain } from '../packages/app/src/pending.ts';
const storage=()=>{let value;return {getItem:()=>value??null,setItem:(_key,next)=>{value=next;}};};
test('timeout keeps original ID across reload and blocks a new operation',()=>{
  const s=storage();beginPending(s,'original-request','release');
  assert.equal(uncertain(readPending(s)),true);
  assert.throws(()=>beginPending(s,'new-request-id','action'),/reconcile-existing/);
  assert.equal(readPending(s)[0].id,'original-request');
});
test('confirmed response preserves earlier release and action history',()=>{
  const s=storage();beginPending(s,'release-request','release');
  settlePending(s,{id:'release-request',state:'running'});
  beginPending(s,'abort-request-id','action');settlePending(s,{id:'abort-request-id',state:'confirmed'});
  assert.deepEqual(readPending(s).map(r=>r.id),['release-request','abort-request-id']);
  assert.equal(uncertain(readPending(s)),false);
});
test('corrupt storage and unknown response fail closed',()=>{
  const s=storage();assert.throws(()=>settlePending(s,{id:'unknown-request',state:'confirmed'}),/not-found/);
  s.setItem('key','{"token":"synthetic"}');assert.throws(()=>readPending(s),/index-invalid/);
});
