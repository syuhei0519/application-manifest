import test from 'node:test';
import assert from 'node:assert/strict';
import { observationTime } from '../packages/app/src/observation.ts';

test('missing measurement response renders an explicit missing window', () => {
  const verdict = { phase: '解析不能', reasons: ['measurement-value-missing'] };
  assert.equal(observationTime(verdict.window?.start), '未取得');
  assert.equal(observationTime(verdict.window?.end), '未取得');
  for (const value of [null, '', Infinity, NaN, 1e30]) assert.equal(observationTime(value), '未取得');
  assert.equal(observationTime(1792712107), '2026-10-22T23:35:07.000Z');
});
