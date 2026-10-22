# PLAT-170 専用実環境の操作範囲と承認記録

当初の実装承認は repo 変更・fixture・draft PR までだった。
2026-10-22、以下の具体案（専用 kind、DB/Redis credential、必要RBAC/Istio権限・webhook・証明書、
一セッションのCanary/Abort/API試験、証跡保存後の専用環境・credential・試験データcleanup）に対し、
ユーザーが「許可します」と明示した。親タスクから信頼できる会話証拠として受領した。
この範囲で実行可能。既存環境変更・PR merge・有料資源追加は引き続き未許可。範囲が変わればその部分を止める。
作成時は未実行の承認案だった履歴を残す。生成 YAML・CI 成功だけを独立の承認とは扱わない。

## 対象と期限

| 項目 | 限定対象 |
| --- | --- |
| PC / kind / context | ディープインパクト / `plat170-account` / `kind-plat170-account` |
| app / control | `learning-account-release` / `learning-account-control` |
| 監視 / mesh control | `observability` / `istio-learning-system`（いずれも新規 kind 内） |
| 所有識別 | `learning.local/owner=plat170-account`、kind container ID・作成時刻・API CA・namespace UID を試験記録に保存 |
| 期間 | 承認された一試験セッション。完了・失敗・中止時に専用 kind と credential を破棄。後日継続は再度状態確認 |
| 衝突時 | kind/container/context/namespace が既存なら停止。再利用や上書きをしない |

既存 `learning-20261018-69c763`、既存 kubeconfig の current-context、GitLab/K3s/共有環境/本番は変更しない。
kind 作成には専用 kubeconfig ファイルを指定し、全 Kubernetes コマンドでそのファイルと context を明示する。
外部公開・Ingress・host port mapping は作らない。必要な port-forward は localhost のみ。
既存 PC の CPU/メモリ/ディスクは消費するが、新規クラウド有料資源・ライセンス・CI 枠購入はなし。
既存 CI の課金条件そのものはこの作業で変更しない。追加課金が必要ならその操作を止める。

## 新規 credential の用途

| 対象 | アカウント・権限 | 保管・破棄 |
| --- | --- | --- |
| app namespace Secret `learning-account-db` の `admin-password` | 新しい専用 PostgreSQL の `postgres`。初期 DB/role 作成だけ。Account API には渡さない | 承認後に生成、秘密値を Git/PR/Jira/log/会話へ出さない。専用 kind 内と一時入力だけ。終了時破棄 |
| 同 Secret の `app-password` | `account_app`、LOGIN/NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOINHERIT。専用 `account` DB 内の既存 schema migration と CRUD のみ | Account/migration Pod だけ参照。既存 DB/schema/data への変更なし。試験データと共に破棄 |
| control namespace `argocd-redis` の認証 | 新規 Core Redis の認証のみ。外部接続なし | pinned Core manifest の必要 key を再照合して生成・配置。既存 Redis 認証をコピーしない |
| kind API 証明書 / kubeconfig / SA projected token / Istio CA・workload 証明書 | 新規 kind と下記 SA の範囲。外部 GitHub/Jira credential は不要 | kind/Pod の寿命に限定。専用 kubeconfig は cleanup 時に削除。長期 token 発行なし |
| Core in-cluster 登録 | 新規 Core の cache 対象 namespace を app だけに限定。既存登録は変更しない | 登録 Secret/config の必要 field を固定 Core source と照合してから用意。未確定の bearer token を生成しない |

承認案には DB だけでなく Redis・API/SA/Istio の新規 credential も含む。DB/data rollback は対象外。
DB 初期化/migration の具体的 render と Core 登録の最終形は runtime 前に追加確認が必要。

## RBAC・security の具体案

`bootstrap_proposal.py` は固定 platform source から未適用 YAML と `access-inventory.json` を生成する。
この inventory の各 Role/ClusterRole/Binding/SA が最終承認対象。Secret の生成・取得・配置は行わない。

| SA・設定 | 権限の範囲 |
| --- | --- |
| `learning-account-release/learning-rollouts` | app namespace の RS get/list/watch/create/update/patch/delete、Pod get/list/watch/update、Event create/update/patch、Lease create/get/update。Rollout read、`account-release` の Rollout/status/finalizers update/patch、VS/DR read と `account-release` update |
| 同 controller の informer | app namespace の Deployment/Service/Experiment/AnalysisTemplate/AnalysisRun/Job/Ingress read。通知 CM/Secret read は既存標準の固定名のみ。Account DB Secret は読めない。AnalysisRun create、Service patch、DB/PVC write は付与しない |
| `learning-account-control/argocd-application-controller` の app Role | Rollout/Service/VS/DR get/list/watch/create/update/patch/delete。RS/Deployment/Pod/Event/AnalysisTemplate/AnalysisRun は read のみ。AppProject は app namespace とこの4 kindだけ |
| 同 SA の control Role | control namespace の CM/Secret get/list/watch、Application/status/finalizers/AppProject get/list/watch/update/patch、Event create/list/patch。Core 自身の設定・登録・Redis参照に必要。app namespace の Secret read はなし |
| `observability/otel-collector` | app namespace の Pod get/list/watch のみ（UID→実 rollout hash）。監視 namespace 外の他 resource 権限は増やさない |
| Prometheus | 新規 headless Service の DNS A 検出で全 destination sidecar を scrape。追加 Kubernetes API 権限なし |
| `istio-learning-system/istiod` と Istio base | pinned Istio 標準 ClusterRole/Binding・CRD・mutating/validating webhook。cluster 全体の Pod/Node/Service/Namespace/Endpoint/EndpointSlice/Secret/Istio resource read、TokenReview/SubjectAccessReview create、webhook更新、Istio/Gateway関連 status 等の書込みを含む。詳細は生成 inventory。これは namespace 限定権限ではない |
| Istio discovery / injection | discoverySelectors は app と mesh control のみ。revision `learning-1-31-2` opt-in。これは RBAC の縮小ではない。proxy-init の NET_ADMIN/NET_RAW と workload 証明書発行も承認対象。既存 namespace の security policy は変更しない |

Kubernetes RBAC の create は resourceNames だけでは制限できない。RS/Pod も所有者単位で権限制限できない。
専用 namespace と専用 cluster が前提。SA に cluster-admin を追加する案ではないが、Istio 標準権限は広い。
bootstrap 担当の cluster 作成・CRD/RBAC/webhook 適用権限も、新規 kind に限定して確認する。

## 新旧 image pin の確定方法

1. backend 新版 source を `f0425a9c0a70c200fbeb5a549bc8b631bf5a1906` に固定。旧版候補 source は
   `e030275abbf52ecd27b7152ce597b3d89be3f830`。対象 source と既存 build/scan 契約を照合する。
2. PC で既存旧 image の config digest `sha256:864b695a3b0bb730e8a6fa8514fdde3b245204e8b23d5ece37fb21f8d8642684`
   は確認済みだが RepoDigest は空。これを OCI manifest digest と呼ばない。
3. 隔離 build 出力を OCI archive/layout として保存し、manifest bytes の SHA256 と config/platform/source/build証跡を照合。
   source provenance を検証できない既存旧 image は流用せず、固定旧 source から再 build する。
   image scanning も既存の固定 tool/閾値に従い、その結果を catalog の出所に記録する。
4. 新旧で異なる manifest digest、config digest、linux/amd64、source40桁、build/scan証跡を reviewed catalog に固定。
   `verified=true` を書くだけでは証明にならない。fixture の `example.invalid` を runtime に使わない。
5. 承認後、専用 kind に import し、node runtime の repo digest と実 Pod imageID の対応を照合。
   Chart は `repo@sha256:...` のみ。ローカル tag/config ID への差し替えで起動を通さない。
   registry が必要になれば、local only の設定を別途確認し、公開 registry push や新規 credential を追加しない。

PostgreSQL も既存 upstream manifest `sha256:9b18b78397054fce88a9552e9d5a3ad5bb7fd258c5b3cc1c5028e46373d6ea8f`
と platform/config の対応を再確認する。Core/Rollouts/Istio/監視は platform 固定 source の digest を継承する。

## cleanup の承認対象

記録済み kind container ID・専用 context/API CA・namespace UID と照合し、一致する新規 kind だけを削除する。
停止中 operation があれば先に状態を記録。試験証跡は秘密値を除いて保持する。
新規 kind の DB/Redis/SA/証明書/RBAC は cluster と共に破棄し、専用 kubeconfig・一時 credential 入力を削除する。
既存 Docker image cache や既存 cluster は削除しない。cleanup の対象削除も上記一試験セッションの承認に含める案。
所有不一致や削除結果が不明な場合は read-back し、推測で再試行しない。
