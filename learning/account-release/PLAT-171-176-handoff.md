# Account release MVP: PLAT-171〜176 再開 handoff

## 目的と境界

対象は Jira-native PLAT-171〜176（ID 10181〜10186）、site https://syuhei0519.atlassian.net、
cloud ID `0e029d88-1812-4bd9-b5de-bdecfff6740b`。2026-10-23に6件の現行本文・AC・依存を再読し、
変更なし・進行中を確認した。作成時の計画のみ/execution-pausedという本文は履歴として保持し、
今回のrepo変更・テスト・ビルド・draft PR更新への明示承認とPLAT-168 resolverで実行範囲を照合した。

- repo: `syuhei0519/application-manifest`
- 固定 main: `e26d2b43140c64a602eecca7844f0c006ada3fc6`
- 契約repo main: `21fc5ae1daf73efede5b14dc51adf754f9b3c84e`
- branch: `feat/plat-171-176-account-release`
- [draft PR #43](https://github.com/syuhei0519/application-manifest/pull/43)
- 再開前 head: `66a1d4e1133b555c16d5a6f32add98e7152b2961`

新規環境・credential・権限・token更新は今回の再開対象外。PRは未統合であり、6件ともDoneにしない。
最小BackstageはAccount機能だけを対象とし、PLAT-105/117/118/119全体の完了やPLAT-177以降を含めない。

## できたこと

`release_modes.py`とchartが固定digest・Canary manual/auto・Rollingを共通契約にする。
`operations.py`は要求ID、SQLite排他、監査、通信不明結果の照合を担当する。
`control_adapter.py`は固定Git profileのdraft PR、審査済みGitの全差分検査、Application CAS、Rollout操作を接続する。
UI入力からrepo/path/ref/namespace/任意commandを受け付けず、Applicationのproject/destination/source全体を検証する。
`evidence_verifier.py`・`record_observation.py`・`verified_state.py`は実測receiptを検証して原子的に保存する。
時刻・UID・generation・revision・digest・traffic・healthが揃うまで完了しない。
正常終了からの復元は新鮮なcompletion、Abort後はrecoveryを要求し、レビュー中の期限切れも反映前に拒否する。

`backstage/`は公式Backstage 1.56.0による固定Account/localの最小画面・backend。
標準設定は書込みとguest認証が無効。固定Node 24.22.1/Yarn 4.13.0、lockfile、型検査・ビルドをCIへ追加した。
画面にdigest/mode/diff、要求段階、Promote/Abort、永続復元、指標・判定理由・履歴を表示する。
不明結果のIDを保存し、reload後も盲目的に再送せず再照合する。

## ACと証拠の対応

AC番号は現在のJira本文の箇条書き順。fixture成功は実環境受入完了を意味しない。

| Jira / AC | 実装・fixtureの確認 | 未実証の受入条件 |
|---|---|---|
| 171 AC1 | 固定digest/modeとGit/Helm/runtime対応。contract・release_modes・adapterテスト | 最終コードのUI→Git→runtime確認 |
| 171 AC2 | manual/auto、Rollingのmode/Promote拒否、UI非表示 | 実画面の両方式操作QA |
| 171 AC3 | 未終端、stale generation/version、drift拒否 | 実traffic中の切替拒否 |
| 171 AC4 | 旧版保持の段階的switchとsubset境界のreceipt検証 | Canary→Rolling→Canary実切替、前版identity保持 |
| 171 AC5 | Rolloutのreadiness/失敗/完了投影 | Rolling失敗・完了をUIと実状態で照合 |
| 172 AC1–2 | Account/local/catalogのみ、preview差分とSHA、review前は適用しない | UI選択→diff→draft PR正常操作 |
| 172 AC3–4 | 審査済みPR全差分検査、要求段階、ID/SQLite/CAS、重複・再起動fixture | 実Git/ArgoCD開始、二重クリック・キャンセル・中断復帰QA |
| 172 AC5–6 | actor/time/digest/mode/PR/commit監査、秘匿エラー、backend固定対象 | 最終コードで実権限による接続・監査照合 |
| 173 AC1–2 | manual pauseのみPromote、managed releaseのみAbort、永続復元と分離 | UIから10→50→100、途中Abortと回復測定 |
| 173 AC3–5 | 排他・stale・終端・二重送信・timeout照合、Rollout status CAS、監査fixture | 実操作競合/遅延/再送時のUIとcontrollerの一致 |
| 173 AC6 | UI非依存controller、reload時の要求ID復旧 | UI停止・復旧を含む実操作再開 |
| 174 AC1–2 | 新旧/要求digest・desired SHA・generation、revision別window/sample/更新時刻 | 同じ実測windowとUI数値の照合 |
| 174 AC3–4 | missing/low/NaN/timeout/stale/historicalを成功扱いしない共通verdict | manual/autoの全表示分岐QA |
| 174 AC5–6 | 相関履歴、発生時刻と取得時刻、到達不能のfail closed | 実権限不足・接続断・復帰時のQA |
| 175 AC1 | 版管理policy、Python/Go共有cases、固定window/sample/閾値 | 最終CI照合 |
| 175 AC2 | 正常auto10/50/100成功。異常AR Failed→Abort→stable100/canary0を観測 | Abort後API回復測定は未実施。旧実測は下記固定版限定 |
| 175 AC3 | 低traffic/欠測/NaN/query error/timeoutはInconclusive、無限retryなし | 低traffic・完全欠測で実pause |
| 175 AC4–5 | manual共通schema、途中mode変更拒否、controller独立 | UI/API停止・復旧後の表示一致 |
| 176 AC1–3 | 検証済み前版のみ、理由/diff、Git review、Abort後予約移譲、成功後復元、stale/重複拒否 | UIから前版Git変更・反映・復元中の排他 |
| 176 AC4–6 | desired/runtime/traffic/health照合、再syncとAccount/両controller再起動receipt、部分失敗再照合 | 不良版→Abort→前版Git→再sync・controller再起動→API回復 |

## 検証方法と独立レビュー

LinuxでhelmをPATHへ置き、PyYAML 6.0.3/jsonschema 4.26.0を使用する。

```sh
python -m unittest discover -s learning/account-release -p 'test_*.py' -v
go -C learning/account-release/metric-plugin test -count=1 -timeout 90s -v ./...
cd learning/account-release/backstage
yarn install --immutable
node --test tests/*.test.mjs
yarn typecheck
yarn build
```

2026-10-23再開時のPython/Helm回帰59件、Nodeの欠測時刻・pending/reload関連4件、
TypeScript、production buildはすべて成功した。Backstageソースの再スキャンも既存policyでPASS（依存の警告あり）。
最終SHAのCI結果はPR checks・PR本文/Jira追記を正とする。
独立レビューはPython adapter/coordinator/receipt/atomic state、UI/backend/権限設定、CI/chartを対象とした。
正常完了後の前版復元が到達不能というP2を、completion/recoveryの使い分けと3件の追加回帰で修正した。
再レビューで解消・追加blockingなしを確認した。静的レビューは実環境ACや人の統合許可の代わりではない。

## 前回1セッションの実測と限界

専用kind-plat171-accountで、旧backend source `e030275abbf52ecd27b7152ce597b3d89be3f830`、
新source `f0425a9c0a70c200fbeb5a549bc8b631bf5a1906`を使用した。

- 旧image: `ghcr.io/syuhei0519/backend-app@sha256:d48a702e247e884d0da54cd8d9a09b58ba45aa7fe76627014f1135313bc1a86e`
- 新image: `ghcr.io/syuhei0519/backend-app@sha256:f9a3dfd69853050d72647609eb2ee1493ed41bf17fb7c6c2abbf068361cc88df`
- plugin入りcontroller: `learning.local/account-rollouts@sha256:966adba896524519bd1653b31ade97b624dd5cbf83623f7cdeb5c5708aaa69e0`
- 実配置plugin SHA256: `51c6505758ba09230478c65319b2e0696f9bad3b0e16802238823a7f361920db`

pluginは公式Rollouts v1.10.1 API/RPCを用いた今回の独自実装で、metrics読取りだけを持ちGit書込み権限を持たない。
配置前のimage/archive/report identityとpolicy gateを照合した。スキャンは警告付きPASSで、脆弱性ゼロではない。
実ビルドのquery入力はCRLF、GitはLFでJSONとして同値。CIバイナリとbyte一致という証明はない。

正常autoのGitは `a22e80e3d6d76d83ec29877c45a3e3c2ec53f27a`。
10/50/100 AnalysisRun成功、追試HTTP192/192が200・retry0・250ms超0、API12ケース成功を保存した。
長時間client receiptは未取得であり成功に数えない。
正常試験時のArgoCD OutOfSyncについて、paused=falseとTCP defaultの差をchartで修正した。
修正版Git `9a7eb049d33bc2a4e1cf4e116a11dd9d9ac7aa6e` の異常試験は2154件のHTTP400、
AR Failed、自動Abort、stable100/canary0、App Synced/Succeededを観測した。
Abort後API回復試験は拒否により未実行。最終修正コードの全シナリオ実測完了とは扱わない。

raw snapshots・log・過去の拒否記録は作業PCの証跡領域に保持する。
Gitには再現方法と結論を残し、private実行identityを含む生成JSONや秘密を追加しない。
再開前39ファイルと保存zipの一致を確認し、zipと元の証跡を保持した。

## 清掃と次の担当者

過去の清掃拒否は履歴として保持する。その後、限定した新しい人の承認で清掃が完了し、
2026-10-23 04:27 UTCに専用kind、所有Backstageプロセス、loopback3000/7007、
専用token/kubeconfig/署名鍵/SQLite・試験DBの不存在を確認した。
既存kind `learning-20261018-69c763`、既存context設定、ソース39ファイル・証跡・バックアップを保持した。
tokenは再発行していない。保存済みイメージ/cacheは保持した。

次の担当者は、未実証ACに対する新しい限定試験計画と環境・credential・権限の承認を取得する。
承認前に環境を再作成しない。実画面QA（繰返し・stale・キャンセル・中断復帰）、両方向方式切替、
低traffic/欠測pause、永続Git復元と再起動後API回復を実証する。
独立レビュー・最終SHA CI・人のPR統合許可・統合後確認も揃うまでJira Doneにしない。

## 学び

静的fixture・観測済みtraffic・Git統合・cleanupは別々の証拠である。
Abortによるtraffic退避を永続復元の成功に数えず、成功後の前版復元にも新鮮な実測証跡を要求する。
通信不明操作は再実行より先に状態を照合し、試験許可は一回のセッションを越えて再利用しない。
