# Account release modes / controller policy（PLAT-171・175）

基底 main は `e26d2b43140c64a602eecca7844f0c006ada3fc6`。2026-10-23 の専用 kind / Backstage
試験は今回の明示承認で実施し、14:12 UTCに実機検証と専用環境の清掃を完了した。
コード `abed735b467c727a3d6941c6c1495d0f34e1bcc1` はPython/Helm66件、Portal12件、typecheck、
production build、同一headのPR/push CI、独立レビューに合格。手動Canary、双方向方式切替、
Rolling readiness/期限超過/回復、自動正常・異常Abort・低traffic/欠測停止、Git永続復元と再起動維持を実証した。
AC別証拠・制約・最終復元・清掃結果は [受入記録](ACCEPTANCE-PLAT171-176.md) を参照する。
main統合・統合後CIは別途最終確認する。コードやCI成功だけでAC完了とはしない。

## 共通設定と切替

`release_modes.py` は Account/local、reviewed digest、期待 Git SHA/generation/version を照合する。
Canary は manual/auto を指定し、Rolling は mode を持たない。非終端・drift・古い要求は拒否する。
Abort/進行期限切れは recovering であり、新しい release を開始できる終端ではない。
`runtime_status` は実 Rollout の name/namespace/期待 UID、観測 generation、replica/condition/hash を照合する。
Rolling は metrics の合否を readiness の代わりに使わない。

`switch_plan` は Git 変更の計画を返す。実行済みの証拠や排他制御ではない。
Canary→Rolling は旧 image を維持し、subset-free route、Istio 管理解除、空 subset と実トラフィックを確認後に新 image へ進む。
Rolling→Canary は subset-free route のまま旧 hash の両 subset を作り、確認後に旧 image の Canary を有効化してから新 image を開始する。
各 phase の実状態確認と Git CAS は後続 adapter が必須。計画全体の一括 apply は不可。

2026-10-23 の切替修正: Rolling の VirtualService は wave -1、Rollout は wave 0、DestinationRule は wave 1 とし、
全量経路を先に反映してから Istio 制御を解除し、最後に subset を空にする。後続 seed 段階へ進む前に、実際の
`argo-rollouts.argoproj.io/managed-by-rollouts` annotation が存在しないことを測定前後で確認する。
AnalysisTemplate は Rolling 中も保持するが Rollout から参照せず、prune を必要とする orphan を残さない。
Canary の DestinationRule は初回同期でも先に必要なため wave -1 とする。

Argo CD は CRD の差分除外値を merge patch で戻す際、VirtualService の配列全体を旧構造に戻す場合がある。
Rolling→Canary の `activate-mode` は同一旧 image、観測 hash、正常状態、Git 一致、90秒以内の実測証跡を再確認し、
その一度の同期だけ `RespectIgnoreDifferences=false` でレビュー済みの初期100/0経路を反映する。
永続 Application 設定と通常の image 更新・再同期は `RespectIgnoreDifferences=true` を維持する。
固定対象、UID/resourceVersion/spec の CAS、`prune=false`、レビュー・CI・実測ゲートは維持する。
これは同期時の配列構造を正しく更新する処理で、controller が管理する進行中の重みを Git に戻す操作ではない。

`desired_documents` は offline fixture 用。実運用では PLAT-170 の固定 Git profile が正本であり、
Application values override を実行経路へ転用しない。`runtime_support.py` は mode を含む commit 済み profile を読み、
AppProject/ignoreDifferences を同じ設定から生成する。未コミット checkout は使わない。操作adapterは固定repo/path/refと対象Applicationを検証し、各段階をライブで確認する。

## controller による判定

`analysisPluginEnabled=true` の Canary は各10/50/100段階に同じ AnalysisTemplate を使う。
manual は分析後に通常 pause、auto は分析成功後に次段階へ進む。auto は plugin 有効化なしでは render を拒否する。
各 AnalysisRun 作成から300秒後の5分窓を使い、最低20要求、availability 99%以上、250ms以下の比率95%以上を要求する。
100%段階は candidate を評価し、idle stable の欠測は合否に使わない。

plugin は今回の独自実装。公式 Rollouts v1.10.1 の API/RPC を利用する。
実 Rollout/AnalysisRun/step/VS/DR/RS/Pod と、Pod UID・imageID・source commit・SDK instance を照合し、測定前後の同一性を確認する。
Prometheus query は同一時刻、各5秒 timeout。query失敗・欠測・NaN・低traffic・identity不明は Inconclusive、
有効な error/latency 違反は Failed。片方の欠測で別の有効な違反を隠さない。`policy-cases.json` を Python/Go が共有する。

AnalysisTemplate は count=1、failureLimit=0、inconclusiveLimit=0、consecutiveErrorLimit=0。
自動再試行はしない。Inconclusive は controller pause とし、再評価は新しい AnalysisRun と完全な窓を必要とする。
自動再評価/再開は提供しない。固定対象の明示Abortと、freshなstable回復証跡を確認したGit前版復元を別操作で行う。
Abort は別の明示操作であり、Git desired の永続復元とは別。
plugin起動/RPC障害は上流仕様の Error/Abort で、計測不足と区別する。Resume/Terminate は未確定測定を黙って再送しない。

判定値は `account-verdict-v1` JSON に role別値、窓、閾値、理由、AnalysisRun UID、実Pod identityを保存する。
秘密値は含まない。PLAT-174の画面はこのcontroller記録を表示し、同一窓のPrometheus応答と実機で照合した。

## 配置提案と供給元

`analysis_proposal.py` は固定 bootstrap の未適用 delta を作るだけで、credential や Kubernetes API を扱わない。
Rollouts は release namespace の AnalysisRun と status/finalizers への必要更新、ArgoCD は同 namespace の AnalysisTemplate 書込みだけを追加する。
公式固定 controller に `metric-plugin/Dockerfile` で独自 binary を追加したローカル OCI image を使う。外部 registry に publish しない。
`file:///opt/account-policy` を登録し、既存 emptyDir plugin cache を使う。hostPath を使わない。
固定 upstream の file:// コピーは sha256 を検証しないため、独自 binary の起動時に `ACCOUNT_POLICY_SHA256` と自己ファイルを照合し、不一致は起動拒否する。

ビルダーは `golang:1.27.1-bookworm@sha256:648f440f42a0958804efb24df176f806f9d353b41f1c0627f666428e40310f6b`。
依存は go.mod/go.sum に固定。`CGO_ENABLED=0 go build -trimpath -buildvcs=false -o account-policy .` でビルドし、
source SHA・binary SHA256・OCI digest・scan・独立レビューを確定してから承認済み専用環境へ配置する。専用kindでのみ配置・検証し、試験後に削除した。

## 再現と初期実装時の記録

```sh
HELM=helm python -m unittest discover -s learning/account-release -p 'test_*.py' -v
go -C learning/account-release/metric-plugin test -count=1 -timeout 90s -v ./...
# 固定 platform checkout と未適用 bootstrap を指定
python learning/account-release/validate_upstream.py --platform "$PLATFORM" --proposal "$PROPOSAL" --output "$NEW_OUTPUT"
```

以下の未実施・未承認表記は2026-10-22時点の履歴で、現状は冒頭と受入記録を正とする。

2026-10-22: Python/Helm 24件、Go11テスト関数・全分岐、固定 upstream schema の manual/auto/Rolling/seed が成功。
固定 Promtool は23 queryの構文と混合欠測fixtureが成功し、HTTP 100要求/availability 0.9/latency空を確認した。
独立レビューは最終query差分を含め追加blockingなし。旧170 queryの代表3構成はbaseと完全一致。
未配備linux/amd64 binary SHA256は `51c6505758ba09230478c65319b2e0696f9bad3b0e16802238823a7f361920db`。
最終コードの CI は [draft PR #43](https://github.com/syuhei0519/application-manifest/pull/43) で照合する。

| AC | 静的証拠 | 未実施 |
| --- | --- | --- |
| 171-1 共通設定 | schema/mode/immutable digest・固定Git profile fixture | 操作runtime adapter接続 |
| 171-2 入力拒否 | Rolling mode拒否・制御なしrender | Backstage表示/操作 |
| 171-3 排他/stale | 非終端・drift・generation/version拒否 | 永続排他adapter |
| 171-4 次回切替 | 旧identity維持のphase計画/fixture | 隔離kind双方向切替 |
| 171-5 Rolling状態 | 対象UID/実replica/condition判定 | 実状態表示・進行失敗実測 |
| 175-1 版管理policy | 固定窓/閾値/共有fixture | 最終配備pin |
| 175-2 正常/異常 | Go判定分岐/RPC/HTTP | auto完遂/Abort stable復帰実測 |
| 175-3 判定不能 | 欠測/NaN/低traffic/timeout・一測定 | controller pause・有界再評価adapter |
| 175-4 manual共通 | 同Template/schema/fixture | UI接続 |
| 175-5 UI独立 | controller plugin構成 | UI/API停止・復帰中の実測 |

172/173/174/176 は Backstage 基盤の所在/最小追加範囲の確認待ち。177以降を開始しない。
新規 kind/credential/RBAC/security 操作と PR merge は未承認。未検証AC・未統合PRを Done としない。


## 2026-10-23 再開結果への追記

上記は作成・初期静的実装時の履歴です。最小Backstage、Git/操作adapter、実測receipt検証、
部分的な専用kind試験と清掃、最新のAC別未実証項目を
[PLAT-171-176-handoff.md](PLAT-171-176-handoff.md) に記録しました。
この追記も当時の状態を示す。2026-10-23最終実機受入と清掃の結果は冒頭の受入記録へ集約した。
