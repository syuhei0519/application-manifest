import test from 'node:test';
import assert from 'node:assert/strict';
import { observationTime, rateText, errorRateText } from '../packages/app/src/observation.ts';

test('missing measurement response renders an explicit missing window', () => {
  const verdict = { phase: '解析不能', reasons: ['measurement-value-missing'] };
  assert.equal(observationTime(verdict.window?.start), '未取得');
  assert.equal(observationTime(verdict.window?.end), '未取得');
  for (const value of [null, '', Infinity, NaN, 1e30]) assert.equal(observationTime(value), '未取得');
  assert.equal(observationTime(1792712107), '2026-10-22T23:35:07.000Z');
});

test('error rate uses the measured availability while invalid rates stay missing', () => {
  assert.equal(errorRateText(0), '100.00%');
  assert.equal(errorRateText(1), '0.00%');
  assert.equal(errorRateText(0.99), '1.00%');
  assert.equal(rateText(0.99), '99.00%');
  for (const value of [undefined, null, NaN, Infinity, -0.1, 1.1, '0']) {
    assert.equal(errorRateText(value), '欠測');
    assert.equal(rateText(value), '欠測');
  }
});
