# Account リリースの最小 Backstage 基盤

既存kindへの統合対象（PLAT-194/195/177）。完了済みPLAT-172〜174/176の部品を再利用する。固定対象 `component:default/account` / `local` のみを扱う。
Backstage 1.56.0、Node 24.22.1、Yarn 4.13.0 を固定し、依存は `yarn.lock` に保存する。
旧 Backstage 基盤チケット全体の完了を意味しない。

## 起動と試験の境界

既定の `app-config.yaml` は書込み無効・guest 無効である。
試験では別途承認された private config に限り guest provider を有効にし、
frontend 3000 / backend 7007 を 127.0.0.1 に束縛する。本人確認を行わないため、
この PC の loopback に接続できる利用者は承認された試験操作を実行できる。
秘密、試験用 kubeconfig、SQLite、署名鍵は Git に保存しない。

`access-proposal.yaml` は既存kindの限定SA/Role。固定contextは
`kind-learning-20261018-69c763`、Accountは`learning-account-release`、
既存Coreは`learning-progressive-control`。別kind/controllerは作らない。
SA tokenは最大1時間。承認済みの最大24時間枠内で同じSA/scopeだけを手動更新でき、
返却expiryと絶対終了時刻をadapterが検査する。期間外発行・無人更新はしない。
GitHub は Windows の既存承認済み wrapper をサーバから利用する。
API 入力にはコマンド、repo、path、ref、namespace、destination、project を受け付けない。
Applicationの対象全体を固定検証する。Git所有のdefinitionをmainへCAS同期し、
子workload Applicationの確定SHA/ignore設定を確認して、そのSHAのoperationだけをCAS同期する。
Application spec/source/ignoreはpatchせず、native policyも操作境界を強制する。

## 要求・観測・復元

画面は固定 digest と Canary/Rolling を選択し、差分確認後に draft PR を要求する。
方式切替は旧版を保持した段階別のレビューとなる。PR の自動マージは実装しない。
Promote は手動 Canary の通常 pause のみ、Abort は同じ管理対象 release のみ。
SQLite の予約・要求 ID・監査履歴を先に保存し、通信不明時は再送せず照合する。
画面を閉じても保存した要求 ID で復帰できる。

実測 receipt は UI の入力ではない。同じ固定kind/Account namespaceの独立observer が、固定対象の
before/after snapshot、200件のHTTP結果、Envoy counter窓を取得する。
`../record_observation.py --config <private adapter.json> --receipt <receipt.json>`
で live identity と `evidence_verifier.py` の検証を繰り返して取り込む。
UI要求の完了証跡には `--request-id <保存済みID>` も指定し、統合済みGit差分とphaseを照合する。
この CLI は kube/Git を読み取るだけで、credential・Git変更・Kubernetes patch を作らない。
receipt が不足・古い・混在していれば完了、解除、次の切替段階へ進めない。

反映前に検証済み前版と管理対象 revision をサーバ側へ原子的に保存する。
Abort は traffic 回復だけであり、Git desired version の復元を意味しない。
復元要求は別の draft PR でレビューし、Git同期・実traffic・健康状態を再確認する。
永続復元には同じ desired revision の再同期に加え、Account Pod と ArgoCD/Rollouts
controller の前後 UID・所有関係・Ready の receipt が必要である。
observer の実試験接続と controller 再起動は、この変更だけで実証済みとは扱わない。

## ローカル検証

Yarn immutable install の後、`yarn typecheck`、`node --test tests/*.test.mjs`、
`yarn build`。Python の coordinator / adapter / receipt / state 回帰は親ディレクトリの
`test_operations.py`、`test_control_adapter.py`、`test_evidence_verifier.py`、
`test_verified_state.py`。CI もこれらを実行する。

終了時は今回のPortal-owned processだけを止め、保護したtask kubeconfig/private configを撤去する。
UID照合後、今回の2RoleBindingだけを撤去し、短期JWTは自然失効する（ファイル削除を失効とは扱わない）。
既存Core UI、kind、CA、旧DB/PV、Sonar、新Account DB Secretは削除しない。
DB資格情報は記録した7日以内の手動更新期限を守る。詳細はplatform-gitopsの
`learning/existing-kind/execution-approval.md`と個別Istio追加承認の範囲に従う。
通常操作はGit/PR→Argoのみで、受入完了は実測ACで別途判定する。


## Existing-kind image supply

The server-only bridge config selects `image-catalog.existing-kind.json`, containing
only the two compatible checked main builds. `image-catalog.reviewed.json` retains
historical isolated-trial identities for old fixtures and evidence. Do not offer
those old images through this existing-kind Portal: their telemetry identity guard
is incompatible with the new Collector. Registry publication is not needed.
The operator's initial Rolling bootstrap establishes the healthy baseline; it does
not count as the PLAT-177 UI Rolling acceptance case.
