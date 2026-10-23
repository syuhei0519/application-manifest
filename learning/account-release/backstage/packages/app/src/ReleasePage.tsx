import { useCallback, useEffect, useRef, useState } from 'react';
import { useApi, discoveryApiRef, fetchApiRef } from '@backstage/core-plugin-api';
import { useEntity } from '@backstage/plugin-catalog-react';
import { Content, InfoCard } from '@backstage/core-components';
import { Button, FormControl, InputLabel, Select, MenuItem, Typography, TextField } from '@material-ui/core';
import { beginPending,readPending,settlePending,uncertain, type Pending } from './pending';
import { observationTime as at } from './observation';

type Snapshot = { current: any; catalog: { image: string; source_commit: string }[]; history: any[]; fetched_at: string; verdict?: any; analysis_history?: any[] };
const target = 'component:default/account';
function EvidenceLinks({detail}:{detail:any}) {
  const pr = typeof detail?.pr_url==='string' && /^https:\/\/github\.com\/syuhei0519\/application-manifest\/pull\/[1-9][0-9]*$/.test(detail.pr_url) ? detail.pr_url : undefined;
  const commit = detail?.commit ?? detail?.git_revision;
  return <>
    {pr && <Typography><a href={pr} target="_blank" rel="noopener noreferrer">draft PR / レビュー結果</a></Typography>}
    {typeof commit==='string' && /^[a-f0-9]{40}$/.test(commit) && <Typography>commit: <a href={`https://github.com/syuhei0519/application-manifest/commit/${commit}`} target="_blank" rel="noopener noreferrer">{commit}</a></Typography>}
    {detail?.reason && <Typography>理由: {detail.reason}</Typography>}
    {detail?.image && <Typography>対象 image: {detail.image}</Typography>}
    {detail?.strategy && <Typography>方式: {detail.strategy} / {detail.promotion_mode??'自動更新'}</Typography>}
  </>;
}
const stateNames: Record<string,string> = {
  rolling:'Rolling 更新中（readiness 待ち）', syncing:'同期中', unavailable:'状態取得不可',
  accepted:'要求受付', 'review-pending':'レビュー待ち', 'apply-pending':'反映待ち', running:'実行中',
  paused:'停止中', recovering:'復旧中', 'outcome-unknown':'結果不明・再照合が必要',
  succeeded:'完了', restored:'永続復元完了', 'sync-failed':'同期失敗', 'phase-review-required':'次の切替段階のレビューが必要',
};

const runtimeReasons: Record<string,string> = {
  'runtime-progress-deadline-exceeded':'進行失敗：更新期限を超過しました。原因を確認し、復旧を検証してください。',
  'runtime-aborted':'Abort 済み：stable への復帰を検証してください。Git の復元は別要求です。',
  'runtime-progressing':'更新中です。',
  'runtime-all-replicas-ready':'すべての更新先 replica が Ready です。',
  'generation-not-observed':'controller の更新確認を待っています。',
};

export function ReleasePage() {
  const { entity } = useEntity();
  const discovery = useApi(discoveryApiRef); const api = useApi(fetchApiRef);
  const [snapshot,setSnapshot] = useState<Snapshot>(); const [error,setError] = useState('');
  const [busy,setBusy] = useState(false); const [image,setImage] = useState('');
  const [strategy,setStrategy] = useState('Canary'); const [mode,setMode] = useState('manual');
  const [preview,setPreview] = useState<any>(); const [result,setResult] = useState<any>();
  const [restore,setRestore] = useState(false); const [reason,setReason] = useState('');
  const [pending,setPending] = useState<Pending[]>(()=>readPending(localStorage));
  const [selected,setSelected] = useState('');
  const unresolved=uncertain(pending);
  const inFlight = useRef(false); const mounted = useRef(true);
  const allowed = entity.kind === 'Component' && entity.metadata.name === 'account' &&
    (entity.metadata.namespace ?? 'default') === 'default';
  const call = useCallback(async (route: string, payload?: any) => {
    const base = await discovery.getBaseUrl('account-release');
    const response = await api.fetch(`${base}/${route}`, payload === undefined ? undefined : {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok || !body.ok) throw new Error(body.code ?? 'connection-unavailable');
    return body.value;
  },[api,discovery]);
  const refresh = useCallback(async () => {
    try { const value = await call('state'); if (mounted.current) { setSnapshot(value);setError('');setPreview(undefined); } }
    catch (e) { if (mounted.current) {setError(String((e as Error).message));setSnapshot(undefined);setPreview(undefined);} }
  },[call]);
  useEffect(() => { mounted.current=true; if (allowed) void refresh(); return () => {mounted.current=false;}; },[allowed,refresh]);
  const perform = async (fn: () => Promise<void>) => {
    if (inFlight.current) return;
    inFlight.current=true;setBusy(true);setError('');
    try {await fn();} catch(e) {setError((e as Error).message);} finally {inFlight.current=false; if(mounted.current)setBusy(false);}
  };
  if (!allowed) return <Content><Typography>この画面は Account 専用です。</Typography></Content>;
  const c = snapshot?.current;
  const requestPayload = () => {
    if (!c) throw new Error('current-state-required');
    const request={entity:target,environment:'local',image,strategy,promotion_mode:strategy==='Canary'?mode:null,
      expected_git_revision:c.git_revision,expected_generation:c.generation,expected_version:c.version,
      request_id:crypto.randomUUID()};
    return {kind:restore?'restore':'release',payload:restore?{request,reason,previous_git_revision:c.verified_previous?.git_revision}:request};
  };
  const identity = () => Object.fromEntries(['release_id','git_revision','generation','version','rollout_uid','resource_version','current_step','requested_image'].map(k=>[k,c[k]]));
  const begin=(id:string,kind:string)=>{beginPending(localStorage,id,kind);setPending(readPending(localStorage));setSelected(id);};
  const settle=(value:any)=>{settlePending(localStorage,value);setPending(readPending(localStorage));setResult(value);};
  const runtimeAction = (operation: string) => perform(async()=>{
    const payload={request_id:crypto.randomUUID(),operation,entity:target,environment:'local',expected:identity()};
    begin(payload.request_id,'action');
    const value=await call('action',payload);settle(value);await refresh();
  });
  const invalidate = () => setPreview(undefined);
  return <Content>
    <Typography variant="h4">Account リリース</Typography>
    <Typography>環境: local ／ 対象: Account API</Typography>
    <Button disabled={busy} onClick={()=>void perform(refresh)}>最新状態を取得</Button>
    {error && <div role="alert"><Typography color="error">接続・操作を確認できません: {error}</Typography><Typography>結果不明の場合は再送せず、要求の状態を照合してください。</Typography></div>}
    <InfoCard title="現在の状態">
      {!c ? <Typography>状態を取得できないため操作を停止しています。</Typography> : <>
        <Typography>実行系の状態: {stateNames[c.state] ?? c.state}</Typography>
        <Typography>状態の理由: {runtimeReasons[c.reason] ?? c.reason}</Typography>
        <Typography color={c.git_runtime_drift?'error':'textPrimary'}>Git との照合: {c.git_runtime_drift?'差分あり。新しい要求と手動操作を停止します。':'固定 revision と一致'}</Typography>
        <Typography>方式: {c.strategy} {c.promotion_mode ?? ''}</Typography>
        <Typography>現在版: {c.current_image ?? '不明'}</Typography>
        <Typography>前版: {c.verified_previous?.image ?? '検証済み前版なし'}</Typography>
        <Typography>要求版: {c.requested_image}</Typography>
        <Typography>Git desired: {c.git_revision}</Typography>
        <Typography>Rollout revision / generation: {c.revision} / {c.generation}</Typography>
        <Typography>画面取得時刻: {snapshot?.fetched_at}</Typography>
        {c.strategy==='Canary' && c.actions?.includes('promote') && <Button disabled={busy || unresolved} onClick={()=>void runtimeAction('promote')}>次段階へ Promote</Button>}
        {c.strategy==='Canary' && c.actions?.includes('abort') && <Button disabled={busy || unresolved} onClick={()=>void runtimeAction('abort')}>Abort・stable へ戻す</Button>}
        <Typography>Abort は一時的な traffic 復帰です。Git の前版復元は下の別要求でレビューします。</Typography>
      </>}
    </InfoCard>
    <InfoCard title="Git 変更をレビューへ送る">
      <Button disabled={!c || busy} onClick={()=>{setRestore(false);invalidate();}}>新しいリリース</Button>
      <Button disabled={!c?.verified_previous || busy} onClick={()=>{setRestore(true);setImage(c.verified_previous.image);setStrategy(c.strategy);setMode(c.promotion_mode??'manual');invalidate();}}>Git を検証済み前版へ復元</Button>
      <FormControl fullWidth><InputLabel id="image-label">固定 image digest</InputLabel><Select labelId="image-label" value={image} disabled={!c || busy || restore} onChange={e=>{setImage(String(e.target.value));invalidate();}}>
        {(snapshot?.catalog??[]).map(item=><MenuItem key={item.image} value={item.image}>{item.image}</MenuItem>)}
      </Select></FormControl>
      <FormControl><InputLabel id="strategy-label">方式</InputLabel><Select labelId="strategy-label" value={strategy} disabled={!c || busy || restore} onChange={e=>{setStrategy(String(e.target.value));invalidate();}}>
        <MenuItem value="Canary">Canary</MenuItem><MenuItem value="Rolling">Rolling</MenuItem>
      </Select></FormControl>
      {strategy==='Canary' && <FormControl><InputLabel id="mode-label">進行方法</InputLabel><Select labelId="mode-label" value={mode} disabled={!c || busy || restore} onChange={e=>{setMode(String(e.target.value));invalidate();}}>
        <MenuItem value="manual">手動</MenuItem><MenuItem value="auto">自動判定</MenuItem>
      </Select></FormControl>}
      {restore && <TextField fullWidth label="復元理由（秘密値を含めない）" value={reason} inputProps={{maxLength:300}} onChange={e=>{setReason(e.target.value);invalidate();}} />}
      <Button disabled={!c || !image || busy || unresolved} onClick={()=>void perform(async()=>{const request=requestPayload();const value=await call('preview',request);setPreview({request,value});})}>変更内容を確認</Button>
      {preview && <div><Typography>レビュー前に配備は開始しません。切替は各段階の実状態確認とレビューが必要です。</Typography><pre>{JSON.stringify(preview.value.diff,null,2)}</pre>
        <Button disabled={busy || unresolved} onClick={()=>void perform(async()=>{const req=preview.request;begin(req.kind==='restore'?req.payload.request.request_id:req.payload.request_id,req.kind);settle(await call('request',req));setPreview(undefined);await refresh();})}>draft PR を要求</Button>
        <Button disabled={busy} onClick={()=>setPreview(undefined)}>キャンセル</Button></div>}
    </InfoCard>
    <InfoCard title="判定と観測値">
      {!snapshot?.verdict ? <Typography>観測待ち／判定情報なし。正常とは扱いません。</Typography> : <>
        <Typography>判定: {snapshot.verdict.historical?'この段階の過去の測定結果':snapshot.verdict.stale?'古い観測': '現在の段階の測定'} ／ {snapshot.verdict.observed_verdict??snapshot.verdict.phase} ／ {snapshot.verdict.reasons?.join(', ')}</Typography>
        <Typography>観測窓: {at(snapshot.verdict.window?.start)} ～ {at(snapshot.verdict.window?.end)}</Typography>
        <Typography>データ取得元: controller metric plugin / {snapshot.verdict.analysis_run} / 段階 {snapshot.verdict.stage}%</Typography>
        <Typography>測定完了: {snapshot.verdict.source_time} / 経過 {Math.round(snapshot.verdict.age_seconds)} 秒 {snapshot.verdict.stale?'（古い観測。現在の成功判定には使えません）':''}</Typography>
        <Typography>基準: 5 分間・各版 20 件以上、成功率 99%以上、250ms 以内が 95%以上</Typography>
        {snapshot.verdict.stage==='100' && <Typography>100% 段階は新版を評価します。旧版の測定結果は下の controller 履歴で確認できます。</Typography>}
        {Object.entries(snapshot.verdict.roles ?? {}).map(([role,v]:[string,any])=><div key={role}>
          <Typography>{role==='stable'?'旧版 (stable)':'新版 (candidate)'} / revision {v.revision}</Typography>
          <Typography>要求数: {v.requests??'欠測'} / 成功率: {v.availability==null?'欠測':(v.availability*100).toFixed(2)+'%'} / 250ms以内: {v.latency_fraction==null?'欠測':(v.latency_fraction*100).toFixed(2)+'%'}</Typography>
        </div>)}
      </>}
    </InfoCard>
    <InfoCard title="要求と操作履歴">
      {(snapshot?.analysis_history??[]).map(h=><div key={h.analysis_run_uid}>
        <Typography>測定履歴: {h.source_time} / controller / release {h.release_revision} / {h.stage}% / {h.observed_verdict}</Typography>
        <Typography>理由: {h.reasons?.join(', ')} / 対象 revision: {h.candidate_revision}</Typography>
        {Object.entries(h.roles??{}).map(([role,v]:[string,any])=><Typography key={role}>{role==='stable'?'旧版':'新版'}: {v.requests==null?'要求数欠測':v.requests.toFixed(1)+'件'} / 成功率 {v.availability==null?'欠測':(v.availability*100).toFixed(2)+'%'} / 250ms以内 {v.latency_fraction==null?'欠測':(v.latency_fraction*100).toFixed(2)+'%'}</Typography>)}
      </div>)}
      <FormControl fullWidth><InputLabel id="pending-label">保存した要求 ID</InputLabel><Select labelId="pending-label" value={selected} disabled={busy} onChange={e=>setSelected(String(e.target.value))}>
        {pending.map(p=><MenuItem key={p.id} value={p.id}>{p.id} / {stateNames[p.state]??p.state}</MenuItem>)}
      </Select></FormControl>
      {unresolved && <Typography role="status">未確定の要求があります。新しい操作の前に結果を照合してください。</Typography>}
      <Button disabled={busy || !selected} onClick={()=>void perform(async()=>{settle(await call('reconcile',{request_id:selected}));await refresh();})}>保存した要求の結果を照合</Button>
      {result?.state==='apply-pending' && <Button disabled={busy || unresolved} onClick={()=>void perform(async()=>{settlePending(localStorage,{id:result.id,kind:result.kind,state:'outcome-unknown'});setPending(readPending(localStorage));settle(await call('apply_reviewed',{request_id:result.id}));await refresh();})}>レビュー・統合済み revision を反映</Button>}
      {result?.state==='phase-review-required' && <Button disabled={busy || unresolved || !c} onClick={()=>void perform(async()=>{settlePending(localStorage,{id:result.id,kind:result.kind,state:'outcome-unknown'});setPending(readPending(localStorage));settle(await call('advance',{request_id:result.id,expected:identity()}));await refresh();})}>次の切替段階を draft PR にする</Button>}
      {result && <div><Typography>要求 {result.id}: {stateNames[result.state]??result.state}</Typography><EvidenceLinks detail={result.result}/></div>}
      {(snapshot?.history??[]).map(event=><div key={event.seq}><Typography>{at(event.at)} ／ 発生源 {event.source} ／ {event.type} ／ 要求 {event.request_id} ／ {event.actor}</Typography><EvidenceLinks detail={event.detail}/></div>)}
    </InfoCard>
  </Content>;
}
