import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

// Replay measured projections through the same presentation component as the UI.
// This is not a claim that the deadline instant was captured in the live browser.
const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../packages/app/src/CurrentRuntime.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const module = { exports: {} };
new Function('require', 'module', 'exports', compiled)(require, module, module.exports);
const { CurrentRuntime, stateNames } = module.exports;
const measured = JSON.parse(readFileSync(new URL('./fixtures/rolling-live-projections.json', import.meta.url), 'utf8'));

function render(index) {
  const sample = measured[index];
  assert.match(sample.source_sha256, /^[a-f0-9]{64}$/);
  assert.equal(sample.generation, 8);
  return renderToStaticMarkup(React.createElement(CurrentRuntime, {
    current: { ...sample.runtime, actions: ['promote', 'abort'] },
    fetchedAt: new Date(sample.captured_at * 1000).toISOString(),
    busy: false, unresolved: false,
    onAction: () => { throw new Error('Rendering must never dispatch'); },
  }));
}

test('measured Rolling readiness renders waiting without Canary actions', () => {
  const html = render(0);
  assert.match(html, /Rolling 更新中（readiness 待ち）/);
  assert.doesNotMatch(html, /<button/);
});

test('measured deadline renders progress failure rather than an Abort claim', () => {
  const html = render(1);
  assert.match(html, /進行失敗：更新期限を超過しました/);
  assert.doesNotMatch(html, /Abort 済み/);
  assert.doesNotMatch(html, /<button/);
});

test('measured recovery shows Ready while request sync failure stays a separate state', () => {
  const html = render(2);
  assert.match(html, /実行系の状態: 完了/);
  assert.match(html, /すべての更新先 replica が Ready/);
  assert.equal(stateNames['sync-failed'], '同期失敗');
  assert.doesNotMatch(html, /<button/);
});

test('a missing actual revision is not replaced with the measured Pod hash', () => {
  const html = render(2);
  assert.match(html, /Rollout revision \/ generation: 不明 \/ 8/);
  assert.match(html, /Pod hash: f9c59c964/);
});

test('actual Rollout revision, Pod hash and release correlation render separately', () => {
  const html = renderToStaticMarkup(React.createElement(CurrentRuntime, {
    current: { ...measured[2].runtime, rollout_revision: '17', release_id: 'release-017', release_request_id: 'request-017' },
    busy: false, unresolved: false, onAction: () => {},
  }));
  assert.match(html, /Rollout revision \/ generation: 17 \/ 8/);
  assert.match(html, /Pod hash: f9c59c964/);
  assert.match(html, /release ID: release-017/);
  assert.match(html, /元release要求 ID: request-017/);
});
