import { Button, Typography } from '@material-ui/core';

export const stateNames: Record<string,string> = {
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

export function CurrentRuntime({current:c,fetchedAt,busy,unresolved,onAction}:{current:any;fetchedAt?:string;busy:boolean;unresolved:boolean;onAction:(operation:string)=>void}) {
  return <>
      {!c ? <Typography>状態を取得できないため操作を停止しています。</Typography> : <>
        <Typography>実行系の状態: {stateNames[c.state] ?? c.state}</Typography>
        <Typography>状態の理由: {runtimeReasons[c.reason] ?? c.reason}</Typography>
        <Typography color={c.git_runtime_drift?'error':'textPrimary'}>Git との照合: {c.git_runtime_drift?'差分あり。新しい要求と手動操作を停止します。':'固定 revision と一致'}</Typography>
        <Typography>方式: {c.strategy} {c.promotion_mode ?? ''}</Typography>
        <Typography>現在版: {c.current_image ?? '不明'}</Typography>
        <Typography>前版: {c.verified_previous?.image ?? '検証済み前版なし'}</Typography>
        <Typography>要求版: {c.requested_image}</Typography>
        <Typography>Git desired: {c.git_revision}</Typography>
        <Typography>Rollout revision / generation: {c.rollout_revision ?? '不明'} / {c.generation}</Typography>
        <Typography>Pod hash: {c.revision}</Typography>
        <Typography>release ID: {c.release_id ?? '不明'} / 元release要求 ID: {c.release_request_id ?? '未記録'}</Typography>
        <Typography>画面取得時刻: {fetchedAt}</Typography>
        {c.strategy==='Canary' && c.actions?.includes('promote') && <Button disabled={busy || unresolved} onClick={()=>onAction('promote')}>次段階へ Promote</Button>}
        {c.strategy==='Canary' && c.actions?.includes('abort') && <Button disabled={busy || unresolved} onClick={()=>onAction('abort')}>Abort・stable へ戻す</Button>}
        <Typography>Abort は一時的な traffic 復帰です。Git の前版復元は下の別要求でレビューします。</Typography>
      </>}
  </>;
}
