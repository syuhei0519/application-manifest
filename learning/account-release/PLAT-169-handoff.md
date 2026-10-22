# Handoff

## 目的
- Source: jira-native
- Jira Site: https://syuhei0519.atlassian.net
- Jira Cloud ID: 0e029d88-1812-4bd9-b5de-bdecfff6740b
- Jira Project: PLAT
- Jira ID: 10179
- Jira: PLAT-169
- Jira URL: https://syuhei0519.atlassian.net/browse/PLAT-169
- Source GitHub: none
- Target Repo: syuhei0519/application-manifest
- Base Commit: 226c9bf68833d3153a78c33f051175918ad7918d
- Authorization: human approval 2026-10-22 PLAT-168-170 repo work

### AC
* 対象Account、kind context/namespace、ArgoCD Application、Rollout、Istio経路、metricsの対象ラベル、固定image digestと前版digestの出所を明示する。共有/本番環境を対象にしない。
* 画面の入力は許可された固定digest、Canary/Rolling、Canary時のmanual/auto。RollingではCanary用promote/pauseを提供しない。
* 開始、段階進行10→50→100%、Abort、前版への永続復元、成功/失敗/判定不能/進行中を状態遷移表とAPI契約にし、操作可否を定義する。
* 新旧別request数・error率・latency、観測window・最小traffic・閾値・欠測/NaN/timeoutの判定、判定理由の表示を定義する。根拠のない数値を既定値として採用しない。
* 同時開始、stale画面、更新中方式切替、UI停止、Gitとruntimeの差分、承認前Git変更の扱いを定義する。
* 代表5ケースと合格証跡を定義: 手動Canary正常、自動Canary正常、異常Abort/復旧、metrics不足pause、Rolling正常。

## やったこと

開始時は credential/権限/環境操作を含まない契約作成として照合した。その後 PLAT-170 の具体的な
専用環境試験を人が承認し、catalog の実証を完了した。これは他の環境への承認ではない。

`CONTRACT.md`、純粋参照モデル `contract.py`、`test_contract.py` と CI を追加した。
UI/API adapter・永続 lock の実装は後続課題であり、この課題は範囲・状態・安全契約を固定する。

| AC | 充足と証拠 |
| --- | --- |
| 1 対象・固定入力 | kind/context/namespace/Application/Rollout/Istio/metrics selector を固定。実 OCI catalog と新旧 Pod imageID は PLAT-170 の session/evidence.json で照合済み |
| 2 入力・mode | 固定 digest allowlist、Canary/Rolling、manual/auto、Rolling で promote/pause 不可をモデルと6テストで検証 |
| 3 状態/API | 10/50/100、AbortとGit永続復元の区別、成功/失敗/判定不能/進行中、操作可否を定義 |
| 4 metrics | 5分/role20件/99%/250ms以内95%/fresh90秒/timeout5秒。欠測・NaN・Inf・timeoutは停止。根拠のない成功値を拒否 |
| 5 安全 | CAS、二重開始、stale、同時更新、UI停止、drift、承認前Git変更禁止を定義し回帰 |
| 6 5シナリオ・切断 | manual成功、auto成功、Abort/復元、metrics停止、Rollingを定義し、UI/API実装の境界を明示 |

## 検証とレビュー

- draft PR: https://github.com/syuhei0519/application-manifest/pull/41
- PR head: `4934eeb18973aafcb21c0a96d2a7c28681c9d0da`
- exact head CI: https://github.com/syuhei0519/application-manifest/actions/runs/39782706337 （success）
- 契約6テスト成功。独立レビュー4指摘を修正し、再レビューで未解決指摘なし。
- PLAT-170 は本ブランチに積んだ draft PR #42。実測により AC1 の実 catalog を補完した。

## 統合・Jira の境界

人の承認は PLAT-168〜170 の実装と専用試験に限定され、merge/auto-merge は含まれない。
PLAT-168 の resolver は draft PR の先行 head で検証済みだが main 統合済みとは扱わない。
Jira は3件とも進行中へ更新済み。旧 execution-paused 記述は作成時履歴として保存し、
今回の明示承認との関係を初期コメントに記録した。他の課題は開始していない。
途中の追加コメントは Atlassian 応答障害で結果不明のため、コメント一覧を取得できるまで再送しない。
最終証跡は本 handoff と draft PR に保存する。統合条件が満たされるまで Done にしない。
