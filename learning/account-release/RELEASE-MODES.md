# Account release modes / controller policy（PLAT-171・175）

基底 main は `e26d2b43140c64a602eecca7844f0c006ada3fc6`。本変更は静的実装段階であり、
新しい専用 kind の受入・Backstage UI・main 統合は未実施。PLAT-170 の一試験セッションの承認は再利用しない。

## 共通設定と切替

`release_modes.py` は Account/local、reviewed digest、期待 Git SHA/generation/version を照合する。
Canary は manual/auto を指定し、Rolling は mode を持たない。非終端・drift・古い要求は拒否する。
Abort/進行期限切れは recovering であり、新しい release を開始できる終端ではない。
`runtime_status` は実 Rollout の name/namespace/期待 UID、観測 generation、replica/condition/hash を照合する。
Rolling は metrics の合否を readiness の代わりに使わない。

`switch_plan` は Git 変更の計画を返す。実行済みの証拠や排他制御ではない。
Canary→Rolling は旧 image/hash の両 subset を残して mode を変更し、subset-free route と実トラフィックを確認後に新 image へ進む。
Rolling→Canary は subset-free route のまま旧 hash の両 subset を作り、確認後に旧 image の Canary を有効化してから新 image を開始する。
各 phase の実状態確認と Git CAS は後続 adapter が必須。計画全体の一括 apply は不可。

`desired_documents` は offline fixture 用。実運用では PLAT-170 の固定 Git profile が正本であり、
Application values override を実行経路へ転用しない。`runtime_support.py` は mode を含む commit 済み profile を読み、
AppProject/ignoreDifferences を同じ設定から生成する。未コミット checkout は使わない。操作 adapter とライブ切替は未完了。

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
再評価/再開 API の排他・回数制限・最新状態照合は PLAT-173 adapter の未実装部分。現段階で UI からの再開を提供しない。
Abort は別の明示操作であり、Git desired の永続復元とは別。
plugin起動/RPC障害は上流仕様の Error/Abort で、計測不足と区別する。Resume/Terminate は未確定測定を黙って再送しない。

判定値は `account-verdict-v1` JSON に role別値、窓、閾値、理由、AnalysisRun UID、実Pod identityを保存する。
秘密値は含まない。PLAT-174 の画面接続と同一窓比較は未実施。

## 配置提案と供給元

`analysis_proposal.py` は固定 bootstrap の未適用 delta を作るだけで、credential や Kubernetes API を扱わない。
Rollouts は release namespace の AnalysisRun と status/finalizers への必要更新、ArgoCD は同 namespace の AnalysisTemplate 書込みだけを追加する。
公式固定 controller に `metric-plugin/Dockerfile` で独自 binary を追加したローカル OCI image を使う。外部 registry に publish しない。
`file:///opt/account-policy` を登録し、既存 emptyDir plugin cache を使う。hostPath を使わない。
固定 upstream の file:// コピーは sha256 を検証しないため、独自 binary の起動時に `ACCOUNT_POLICY_SHA256` と自己ファイルを照合し、不一致は起動拒否する。

ビルダーは `golang:1.27.1-bookworm@sha256:648f440f42a0958804efb24df176f806f9d353b41f1c0627f666428e40310f6b`。
依存は go.mod/go.sum に固定。`CGO_ENABLED=0 go build -trimpath -buildvcs=false -o account-policy .` でビルドし、
source SHA・binary SHA256・OCI digest・scan・独立レビューを確定してから承認済み専用環境へ配置する。現在は未配置。

## 再現と残件

```sh
HELM=helm python -m unittest discover -s learning/account-release -p 'test_*.py' -v
go -C learning/account-release/metric-plugin test -count=1 -timeout 90s -v ./...
# 固定 platform checkout と未適用 bootstrap を指定
python learning/account-release/validate_upstream.py --platform "$PLATFORM" --proposal "$PROPOSAL" --output "$NEW_OUTPUT"
```

2026-10-22: Python/Helm と Go10テスト関数・分岐、固定 upstream schema の manual/auto/Rolling/seed をローカル検証する。
最終コードの CI・独立再レビュー結果は PR と Handoff に追記する。

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
