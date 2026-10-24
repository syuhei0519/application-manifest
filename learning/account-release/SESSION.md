# PLAT-170 専用 kind 試験の再現手順

> Historical learning record. Its manual deployment route is retired for the existing kind.
> Use the [Git/PR → Argo deployment contract](https://github.com/syuhei0519/platform-gitops/blob/main/learning/existing-kind/DEPLOYMENT.md).
> The [original commands at fixed SHA](https://github.com/syuhei0519/application-manifest/blob/01ab7fc8c9c39f204957c6f06d7fc79c400e8b91/learning/account-release/SESSION.md)
> remain available as historical evidence, not as a current execution procedure.


この手順は一試験セッションの記録であり、別セッションの credential / RBAC / cluster 作成承認ではない。
今回の承認と追加 Core Secret の履歴は [APPROVAL.md](APPROVAL.md) に残す。
既存環境・共有環境・本番・PR merge は対象外。実測の最終結果は後述の証跡に記録する。

## 固定入力

- application-manifest main: `226c9bf68833d3153a78c33f051175918ad7918d`
- platform-gitops: `867cb81f071d06f7521b93aa110ae001200142a4`
- 旧版 Git profile: `b4fe15b8ce8e2075d551fb7de41fc3c4b075fb45`
- 新版 Git profile: `b971ef3e310bac85d1899a1816a71f8c46686d1e`
- 新旧 OCI manifest/config/source と CI artifact は `image-catalog.reviewed.json`。過去 scan の期限切れを隠さず、
  2026-10-22 の Trivy 0.75.0・例外なし policy による source/image 再検証を実施した。
- Rollouts CLI 1.10.1 SHA256: `fb28a5ba80939aae8e6cb4c04c1e809d080938a2f0d03d496311fc643b4b92cd`

## clean setup

1. `kind get clusters`、Docker container、専用 kubeconfig の既存有無を確認する。同名は停止条件。
   `kind create cluster --name plat170-account --config learning/account-release/kind.yaml --kubeconfig <専用ファイル>`。
   作成後に node container ID と kube-system namespace UID を採取し、全操作の guard とする。
   既存 current-context を切り替えず、全 kubectl / Rollouts CLI に専用 kubeconfig と `--context kind-plat170-account` を渡す。
2. `bootstrap_proposal.py` に固定 platform checkout を渡し、新しい出力先に proposal を生成する。
   `namespaces.yaml`、`crds.yaml`（server-side apply）、`istio-base.yaml`、`istiod.yaml`、`target.yaml`、
   `monitoring.yaml` の順に適用する。CRD の巨大な last-applied annotation を避けるため CRD は server-side apply。
   force-conflicts は使わない。control/monitoring の namespace に sidecar を注入しない。
3. 明示承認済みの DB/Redis credential をメモリ上で生成し、専用 namespace の未作成 Secret へ `create` する。
   `get -o name` による未作成確認を先に行い、値・Secret object はログ/Gitへ出さない。既存 Secret の apply/上書きはしない。
   DB admin/app と Redis auth 以外をこの操作へ混ぜない。
4. `database.yaml` と `control.yaml` を適用し Ready を確認する。DB は専用 emptyDir で、既存 DB と接続しない。
   別途承認した `argocd-secret`（空）と `learning-in-cluster`（tokenなし、app namespaceのみ、clusterResources=false）を
   未作成確認後 `create` する。`client.yaml` を適用し injected client の2 container Readyを確認する。
5. 既存 backend CI の checked-image artifact を取得し、archive/manifest/config/全layer hash・source labelを照合する。
   PCでアプリを新規buildしない。期限内の source/image scan を確認した上で、専用 node の containerd へ
   `ctr -n k8s.io images import --platform linux/amd64 --local --digests --base-name ghcr.io/syuhei0519/backend-app <archive>`。
   `crictl inspecti` の manifest repoDigest と config ID を catalog に照合。registry公開やtagへの置換はしない。
6. `runtime_support.py --manifest-commit <旧版SHA> --catalog image-catalog.reviewed.json --image <旧版digest> --output <新規出力先>`。
   指定 commit の profile/chart だけから migration と Application を生成する。migration Job を実行し Complete を確認する。
   AppProject/Application を適用し、`operation.sync.revision=<固定SHA>`、`prune=false`、
   `RespectIgnoreDifferences=true` の通常手動同期で旧版を確立する。

## 測定と操作

同様に新版 SHA の Application を生成し、通常手動同期で10% pauseに進む。
Git profileのlabelがdigestと不一致だった中間SHA `22e6b650...` は生成gateで拒否され、clusterへ適用されなかった。
実測に使った新版は上記 `b971ef3e...`。

- 10/50%は warmup20件を除く1000件、100%/Abortは200件。timeout2秒、retry0。専用clientだけが要求を出す。
- baseline/終了時の raw Envoy counter と `timestamp(counter)` を同じ評価時刻で保存し、実整数差分を判定する。
  SDKの追加負荷はこの測定窓の外だけで行う。初回10%測定は補助証跡、前後identity検証追加後の測定を最終証跡とする。
- 前後で固定 Git SHA、observedGeneration、pause/step、RS hash、全Pod UID/imageID、VS/DR、実Envoy route、
  全Pod scrape coverageが一致することを確認。Pod台数をtraffic比率には使わない。
- 各pauseで通常ArgoCD同期を2回実行し、同じweight/hash/実routeが保持されることを検証する。
- pause開始から5分経過後、SDKの5分窓を評価する。10/50%は新旧両role、100%はcandidateのみ。
  `target_info`のinstance→Pod UID/source/hash対応、heartbeat freshnessと全instance被覆を照合する。
  欠測を0埋めして成功にしない。要求数20以上、availability>=99%、250ms以内>=95%。
- 通常 `promote account-release` で50→100へ進む。`--full`を使わない。
  100% pauseまで測定後、承認済み `abort account-release` を一度実行し、旧stable100%の実200件を確認する。
  この試験はAbortを実証するため、candidateの最終昇格は行わない。
- Abortのtraffic確認後に `api_regression.py` をclient内で実行し、list/create/get/update/delete、409/400/404と
  synthetic row削除を確認する。CRUDの意図的4xxはrouting測定窓へ混ぜない。

## cleanup

秘密値を除く証跡を保存後、作成時のcontainer ID・namespace UIDと照合する。
一致する専用kindだけを `kind delete cluster --name plat170-account --kubeconfig <専用ファイル>` で削除する。
専用kubeconfigを正規化した絶対pathで確認して削除し、kind一覧・container不在・既存kind ID維持・
既存current-context維持を確認する。DB/Redis/SA/Istio credential、RBAC、webhook、試験データは専用clusterと共に破棄。
既存image cache/clusterは削除しない。所有不一致・結果不明時は停止し、状態確認前に再試行しない。

## SDK 実測で修正した数値整合

50%の初回SDKは窓のbaselineが外れ要求不足として停止した。追加負荷後、stableのraw request/countは
同じ257件増分だったが、最初のOTLP sampleが1msずれ、5分へ外挿されたincreaseが
285.57459386181296と285.5756516199288に分かれた。raw matrixを保存して原因を確認した。
`051590568a03588183e3abfbf9212d5e574604a8` はrawの同一instance/method/route/status_class集合と累積値一致を追加し、
request/count間の外挿誤差のみ0.01件未満とした。histogram内部の0.001、99%/95%/20件/5分/鮮度条件は維持。
実値・境界・1件差・欠測・NaN/Infを固定Prometheusで回帰した。失敗証跡も保持し、修正後の50% SDKを別途再測定した。
100% SDKの最初の試行は5分前（約294秒）で停止し、369秒後の評価を採用した。

## 保存した受入証跡と結果

[session/evidence.json](session/evidence.json) は選択した非秘密の原本と SHA256 を保存する。
各 stage の traffic raw counter/timestamp、client receipt、前後 runtime snapshot、SDK raw query/result、
通常同期2回の operation と snapshot、API 回帰、fresh scan 公開要約、cleanup が含まれる。
大量の latency 個票と無関係な workload、managedFields は除き、判定対象の identity と raw metrics は保持する。
原本の失敗した SDK 評価も残し、成功結果への置換はしない。

| 段階 | stable / candidate 実要求 | SDK 5分要求数 stable / candidate | error率 / 250ms以内 |
| --- | --- | --- | --- |
| 10% | 899 / 101（1000件） | 1910.3309 / 209.0725 | 両方 0% / 100% |
| 50% | 485 / 515（1000件） | 547.8143 / 563.3688 | 両方 0% / 100% |
| 100% | 0 / 200（200件） | 評価対象外 / 242.0982 | candidate 0% / 100% |
| Abort | 200 / 0（200件） | SDK合否対象外 | routing の非200は0件 |

SDK要求数は Prometheus increase の外挿値で、routing の整数実要求とは用途が異なる。
全 pause で通常 ArgoCD sync を2回実行し、weight/hash/実 route/Pod identity を保持した。
API 回帰は12要求すべて期待 status と一致し、作成した1件を削除、最終 list は空。

2026-10-22 14:44:04 UTC に専用 kind と node container の削除を確認し、その後専用 kubeconfig も削除した。
既存 `learning-20261018-69c763` の node ID は不変。既定 context 取得は前後とも returncode=1・stdout空で、
取得結果が不変だった（有効なcontext名を確認したという意味ではない）。専用 DB/Redis/SA/Istio credential・権限・
webhook・emptyDirデータは cluster と共に破棄した。既存環境・image cache は削除していない。
4個の `session/plat170-*.py` は今回の承認下で使った補助手順の履歴であり、新規セッションを自動承認しない。
固定 namespace UID により削除済み cluster 以外への実行を拒否する。

この実証は draft PR の統合承認を代替しない。Jira は進行中のまま、統合後の運用契約確認が残る。
