# Handoff

## 目的
- Source: jira-native
- Jira Site: https://syuhei0519.atlassian.net
- Jira Cloud ID: 0e029d88-1812-4bd9-b5de-bdecfff6740b
- Jira Project: PLAT
- Jira ID: 10180
- Jira: PLAT-170
- Jira URL: https://syuhei0519.atlassian.net/browse/PLAT-170
- Source GitHub: none
- Target Repo: syuhei0519/application-manifest
- Base Commit: 226c9bf68833d3153a78c33f051175918ad7918d
- Authorization: human approval 2026-10-22 PLAT-168-170 repo work

### AC
* PLAT-169の隔離環境と固定revision/digestを用い、Git desired state→ArgoCD→Account Rollout→Istio stable/canary routingを構成する。
* Accountの新旧revisionを実request数・error率・latencyへ対応付け、別demoの結果やPod数だけをtraffic割合の証拠にしない。
* 手動Canary10→50→100%を実要求で観測し、各段階の新旧versionと重み・実測値を記録する。許容差と必要sampleは契約に従う。
* Abortでstableへの経路復帰を確認し、Account主要APIの回帰を確認する。
* cleanな隔離kindからbootstrap/有効化/試験/cleanupを再現できる設定・手順を残す。一度だけのkubectl変更を完成形にしない。
* ArgoCDとRolloutsの所有fieldを定義し、GitOps reconcileが進行中のtraffic制御を巻き戻さないことを確認する。

## やったこと
実装開始前の照合。新credential/権限/共有環境操作は含まない。

## 観察結果
- AC Evidence: pending; current Jira/AC and human scope verified
- Unapproved Operations: merge, deploy, credentials, permissions
- Stop Reason: none

## 学び
PLAT-168 の実装headを先行fixture確認し利用。main統合済みとは主張しない。
