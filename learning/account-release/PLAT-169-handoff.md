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
実装開始前の照合。新credential/権限/共有環境操作は含まない。

## 観察結果
- AC Evidence: pending; current Jira/AC and human scope verified
- Unapproved Operations: merge, deploy, credentials, permissions
- Stop Reason: none

## 学び
PLAT-168 の実装headを先行fixture確認し利用。main統合済みとは主張しない。
