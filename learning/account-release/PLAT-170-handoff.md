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

開始時の計画のみ・未承認という記録は履歴である。後から具体化した専用 kind/DB/Redis/RBAC/Istio
試験と追加 Core Secret 2件への人の明示許可に従い、clean setup から cleanup まで実施した。
追加 Core Secret は一度自動承認レビューで拒否され、未作成確認と追加許可後の一回だけ再試行した。
既存環境の変更、有料資源、PR merge は行っていない。

Git desired profile、chart、namespace/identity gate、通常 ArgoCD 同期、Rollouts の段階、Istio subsets、
SDK/Envoy の実系列をつないだ。DB/migration/Core 登録、fixed OCI import、API回帰、再現手順と非秘密証跡を追加した。

## 固定 revision と image

- platform: `867cb81f071d06f7521b93aa110ae001200142a4`
- 実同期した旧 manifest: `b4fe15b8ce8e2075d551fb7de41fc3c4b075fb45`
- 実同期した新 manifest: `b971ef3e310bac85d1899a1816a71f8c46686d1e`
- SDK clock整合修正: `051590568a03588183e3abfbf9212d5e574604a8`。実同期した manifest と混同しない。
- 旧 backend source: `e030275abbf52ecd27b7152ce597b3d89be3f830`
- 旧 OCI manifest: `sha256:d48a702e247e884d0da54cd8d9a09b58ba45aa7fe76627014f1135313bc1a86e`
- 新 backend source: `f0425a9c0a70c200fbeb5a549bc8b631bf5a1906`
- 新 OCI manifest: `sha256:f9a3dfd69853050d72647609eb2ee1493ed41bf17fb7c6c2abbf068361cc88df`
- catalog に CI artifact/archive/config/source を固定し、期限切れ scan を採用せず source/image の fresh scan を実施した。

## AC と実測

| AC | 充足と証拠 |
| --- | --- |
| 1 Git→ArgoCD→Account→Istio | clean kindで旧新版の固定Gitを同期。profile/chart/migrationを同じcommitから生成し、実PodのimageIDがOCI manifestと一致 |
| 2 実metrics | Envoy整数counterとtimestampを保存。SDK target_infoのinstance→PodUID/source/hash、heartbeat、全instance coverageを照合。Pod数を比率に使わない |
| 3 Canary10→50→100 | stable/candidate=899/101、485/515、0/200。非200は全0。各5分SDKは20件以上、availability=1、250ms以内割合=1で成功 |
| 4 Abort/API | stable200/candidate0、candidate RS=0、実route旧版100%。CRUD/409/400/404を含む12要求成功、synthetic row削除 |
| 5 clean setup/cleanup | 専用kind新規構築、DB/migration/Core/Istio/監視をReady化。14:44:04 UTCに専用kind/container削除確認、その後kubeconfig削除。既存kind ID/default context不変 |
| 6 所有/reconcile | VS weight2箇所/DR hash2箇所だけをRollouts所有。10/50/100の各pauseで通常sync2回、weight/hash/実route/Pod identityが不変 |

数値、raw query/result、前後snapshot、6回のsync operation、API、fresh scan、cleanupは
[session/evidence.json](session/evidence.json)。再現手順と失敗履歴は [SESSION.md](SESSION.md)。
SDK初回の要求不足、1msのOTLP timestamp差による外挿誤差、100%の5分未満評価を隠さず保存した。
修正ではraw集合/値の一致を追加し、request/count間の外挿誤差だけ0.01件未満へ限定した。
SLI閾値、最低traffic、5分窓、freshnessは緩めていない。

## 検証とレビュー

- draft PR: https://github.com/syuhei0519/application-manifest/pull/42 （base: PR #41 のブランチ）
- 実測 manifest の exact head CI: https://github.com/syuhei0519/application-manifest/actions/runs/39793182413 （success）
- SDK修正の exact head CI: https://github.com/syuhei0519/application-manifest/actions/runs/39796313981 （success）
- 契約/統合15テスト、固定upstream schema17件、PromQL構文、固定Prometheusの実engine回帰が成功。
- 独立レビューのblocking指摘を修正し再確認済み。実traffic/SDK/通常sync/Abort/APIのraw証跡も独立レビュー済み。
- 最終証跡追加commitのexact head CIとcleanup/bundleの最終独立レビューはPRに記録する。
- 未実施: mainへの統合、candidate最終昇格、Git前版への永続復元。後二者は今回のAbort実証と別操作。

## 統合・Jira の境界

人の承認は PLAT-168〜170 の実装と専用試験に限定され、merge/auto-merge は含まれない。
PLAT-168 の resolver は draft PR の先行 head で検証済みだが main 統合済みとは扱わない。
Jira は3件とも進行中へ更新済み。旧 execution-paused 記述は作成時履歴として保存し、
今回の明示承認との関係を初期コメントに記録した。他の課題は開始していない。
途中の追加コメントは Atlassian 応答障害で結果不明のため、コメント一覧を取得できるまで再送しない。
最終証跡は本 handoff と draft PR に保存する。統合条件が満たされるまで Done にしない。
