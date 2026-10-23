import test from 'node:test';
import assert from 'node:assert/strict';
import { invokeCore } from '../packages/backend/src/invokeCore.ts';

test('accepted multi-stage advance survives a 25-second bridge operation', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const calls = [];
  const launch = (executable, args, options, callback) => {
    calls.push({ executable, args, options });
    const timeout = setTimeout(() => callback(new Error('private subprocess details'), ''), options.timeout);
    setTimeout(() => { clearTimeout(timeout); callback(null, '{"ok":true,"value":{"state":"review-pending"}}'); }, 25000);
    return { stdin: { end: value => calls.push(JSON.parse(value)) } };
  };
  const pending = invokeCore('/approved/python', '/approved/core', '/approved/adapter.json',
    { operation: 'advance', actor: 'user:default/account-trial', payload: { request_id: 'saved-request' } }, launch);
  t.mock.timers.tick(25000);
  assert.deepEqual(await pending, { ok: true, value: { state: 'review-pending' } });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].executable, '/approved/python');
  assert.deepEqual(calls[0].args, ['/approved/core/control_bridge.py', '--config', '/approved/adapter.json']);
  assert.equal(calls[1].payload.request_id, 'saved-request');
});

test('a real bridge timeout stays unknown and never exposes subprocess details', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let launches = 0;
  const launch = (_file, _args, options, callback) => {
    launches++;
    setTimeout(() => callback(new Error('secret stderr'), ''), options.timeout);
    return { stdin: { end() {} } };
  };
  const pending = invokeCore('/approved/python', '/approved/core', '/approved/adapter.json', {}, launch);
  const rejected = assert.rejects(pending, { message: 'operation-outcome-unknown' });
  t.mock.timers.tick(120001);
  await rejected;
  assert.equal(launches, 1);
});
