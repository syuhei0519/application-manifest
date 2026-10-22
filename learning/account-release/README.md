# Account GitOps / Istio 統合（PLAT-170）

本ディレクトリの静的実装は [PLAT-169 契約](CONTRACT.md)を Account 専用経路につなぐ。
実環境試験は未実施。[新規 kind・credential・RBAC・security・cleanup の承認記録](APPROVAL.md)の範囲で準備中であり、
render や fixture の成功を 10/50/100% 実測・Abort・CRUD の合格として扱わない。
固定入力は manifest base `226c9bf68833d3153a78c33f051175918ad7918d`、platform
`867cb81f071d06f7521b93aa110ae001200142a4`、backend `f0425a9c0a70c200fbeb5a549bc8b631bf5a1906`。

## 実装と所有

`integration.py application` は固定 manifest SHA と reviewed image catalog から AppProject/Application を生成する。
Git → ArgoCD → chart の Rollout/Service/VirtualService/DestinationRule → Istio subsets が desired state の経路。
chart の初期値には実行可能な image がなく、誤 namespace/name・mutable tag・異なる digest label を拒否する。
ArgoCD は自動同期・prune・finalizer なし。初回 apply、同期、image 変更はその操作への承認後のみ。

| field | 所有者・扱い |
| --- | --- |
| image/source/Pod template/steps/Service・VS host/route/subsets の基本 label | Git / ArgoCD |
| VS `http[0].route[0/1].weight` | Rollouts。2 pointer だけ ignoreDifferences |
| DR `subsets[0/1].labels.rollouts-pod-template-hash` | Rollouts。2 pointer だけ ignoreDifferences |
| RS/Pod/状態・hash | Rollouts/runtime。Git が直接変更しない |

DR の両 subset は bootstrap `unassigned` hash を含む。固定 ArgoCD の配列 field 補正に必要な構造を保持する。
Application と各手動 sync operation の両方で `RespectIgnoreDifferences=true` を確認する。
ignore の範囲を VS/DR 全体や Pod template に広げない。各 pause 中、通常 sync を2回行って
weight/hash を保持する実証が必要。既存 demo の ArgoCD 固定実装テストは参考であり、Account の実証ではない。

## 静的再現

Python 3.10+、PyYAML 6.0.3、jsonschema 4.26.0、既存固定 Helm 4.2.4 を使用する。
`PLATFORM` は上記 SHA の clean checkout、`OUT` と `CACHE` は新規の出力先。
生成物に Secret はない。bootstrap generator は checksum 固定の upstream manifests を取得するが Kubernetes API を呼ばない。

```sh
python -m unittest discover -s learning/account-release -p 'test*.py' -v
python learning/account-release/bootstrap_proposal.py --platform "$PLATFORM" --cache "$CACHE" --output "$OUT/proposal"
python learning/account-release/validate_upstream.py --platform "$PLATFORM" --proposal "$OUT/proposal" --output "$OUT/schema"
# platform lock の Prometheus image 内の promtool で $OUT/schema/queries.yaml を check rules
```

CI は新旧 fixture render、固定 upstream Rollout/Istio/Application/AppProject schema、Kubernetes Service schema、
対象 namespace の権限境界、PromQL構文、既存 security gate を検証する。
`example.invalid` の fixture digest は runtime pin ではない。proposal の Istio/control/monitoring は追加承認用であり、
DB 初期化・migration・Core 登録・実 Pod import の有効化まで完成した bootstrap とは主張しない。

## image 出所（実 OCI bytes を照合済み、実行は未許可）

[image-catalog.pending.json](image-catalog.pending.json) に新旧 manifest/config/source/archive hash と CI 出所を固定した。
旧 source の成功 run は [39086590556](https://github.com/syuhei0519/backend-app/actions/runs/39086590556)。
新 source は [39253620294](https://github.com/syuhei0519/backend-app/actions/runs/39253620294) の
`validate`・`image (backend)`・`ci-result` が成功、publish/propose は cancelled。
`checked-image-1` artifact の OCI archive、manifest/config/全layer digest、source label、公開 scan/SBOM report hash を再照合した。
ローカル旧 Docker config ID とは異なる正規 CI 成果物を採用候補とする。

過去 scan の期限は旧 2026-10-19、新 2026-10-20 で切れている。`verified=false` として実行拒否を維持した。
現行 policy による新しい scan と、専用 kind 内での digest/imageID 対応確認の後に reviewed catalog を確定する。
registry に公開済みとは仮定しない。新規 build をしなくても、検証済み OCI artifact の offline import が候補になる。

## 承認後の試験手順と証跡

次は未実施の受入手順。上記承認に加え、対象 PR の統合/採用 revision と image catalog の確認が必要。
merge は今回の実装依頼に含まれない。手順中の全 kubectl は専用 kubeconfig と `--context kind-plat170-account` を指定する。

1. 同名 kind/context/container が存在しないことと PC の空き資源を確認。`kind.yaml`、固定 node digest、専用 kubeconfig で新規作成。
   container/API CA/namespace UID を保存。生成 proposal の namespaces、CRD、Core、namespaced Rollouts、Istio を承認範囲内で適用。
   Core 登録 namespace は app のみ、VS/DR cache inclusion を照合。bootstrap 失敗時は既存環境に切り替えない。
2. 承認済み DB/Redis credential を配置し、専用 PostgreSQL と既存 Account migration を初期化。
   DB・migration・controller は sidecar injection を明示的に無効化する。Account/client だけ opt-in。
   新旧 OCI を import し manifest/config/platform/source と node/Pod imageID を対応付ける。タグへの退避は禁止。
3. monitoring proposal を適用。Collector は SDK の Pod UID から実 rollout hash を付加する。
   SDK の既存 `k8s.namespace.name=account` は Collector が専用 namespace に正規化する。
   Prometheus の既存 SDK scrape に `envoy-scrape.yaml` を generator で追加し、手作業の一時 kubectl edit を完成形にしない。
   headless Service の全 Pod target が up、SDK instance と RS/hash の対応が完全であることを保存する。
4. reviewed catalog の旧版で Application を生成・承認済み同期し、stable を確立。主要 CRUD を実行し合成 row は削除。
   続いて新版 digest を Git desired に反映した承認済み revision を同期。通常の pause 10% に達するまで観測する。
5. 各10/50/100%で Git commit、Application sync revision、Rollout/RS/Pod hash/image、VS weight、DR hash、
   proxy Endpoint/Route、Prometheus target と raw API response を UTC 時刻付きで保存。
   通常 sync 2回の前後で weight/hash が不変であることを検証。drift/欠測時は進めない。
6. 専用 client Deployment の `client` container で `python3 /work/load.py --phase warmup --weight 10` を実行。
   warmup は別 receipt とし、必要な counter series が全 Pod に存在し安定するまで待つ。
   その後負荷停止状態で counter と `timestamp(counter)` を同じ明示 `time=T0` で取得する。
   `--phase measure --weight 10` で1000件を送信し、終了後に負荷を止め、全 Pod の新しい scrape を待つ。
   両 query を同じ `time=T1` で取得。SDK用の他 client 負荷はこの専用 source_workload から送らない。
7. raw `before/after` と `before_timestamps/after_timestamps`、T0/T1、client receipt の start/end を
   `client_window` に、固定 TARGET、実測 `observed_weights`、新旧実 image を evidence JSON にまとめる。
   `integration.py traffic --evidence ...` は同一系列の counter 差分から新旧分配を判定する。
   10%=1000中60〜140、50%=1000中440〜560、100%=200/200、Abort=stable200/200。
   reset/新規・欠測系列/未知version/非200/不足coverage/古いsample/不一致評価時刻は拒否し、値を補完しない。
8. SDK SLI は `sdk_query_set` に実 stable/candidate hash と Ready instance 数を渡す。
   新旧の5分要求数・error率（1-availability）・250ms以下割合を記録。全 role の5分窓・最低20要求・鮮度を
   [契約](CONTRACT.md)で判定する。100%は candidate のみ、旧版は時刻付き参考値。Pod数を traffic割合に使用しない。
9. 手動の通常 promote で50→100へ進め、同じ手順を繰り返す。`--full` や status 手書きで pauseを飛ばさない。
   別の candidate 試験で Abort を実行し、stable100%・candidate0%を200件とproxy状態で確認。
   list/create/get/update/delete、削除後404、重複/不正入力の Account API 回帰を固定 backend 契約で実施する。
   Abort は Git 前版復元と区別し、承認されていない Git変更や後続課題の自動開始は行わない。
10. 証跡を保存し、APPROVAL.md の所有照合・cleanup を行う。失敗時も秘密値をログに載せず、残存 resource を記録。

Prometheus instant API の `value[0]` は評価時刻であり、sample鮮度の証明ではない。
同一 query 時刻の [`timestamp()`](https://prometheus.io/docs/prometheus/latest/querying/functions/#timestamp) の値を照合し、
終了側 raw sample が client の終了を覆うことも検証する。

## AC 状態

| AC | 静的に実装した内容 | 未充足の実証 |
| --- | --- | --- |
| 1 Git→ArgoCD→Account→Istio | 固定対象chart/App生成・新旧OCI出所・bootstrap未適用案 | fresh scan、専用環境での有効化・実同期 |
| 2 実新旧metrics対応 | PodUID/hash SDK query・Envoy destination_version/counter verifier | 実Pod/imageID/hash/series 対応 |
| 3 10→50→100実要求 | 契約sample/許容帯・専用client・実counter検証 | 各段階の実測結果 |
| 4 Abort/API回帰 | stable200/200判定・回帰手順 | Abort経路復帰・CRUD |
| 5 clean kind再現 | kind設定、固定Core/Istio/監視/権限proposal、手順・cleanup案 | DB/migration/Core登録の最終有効化とclean環境通し実行 |
| 6 所有field/reconcile | 4 pointer・RespectIgnoreDifferences・固定schema | Accountで2回syncして重み/hash保持 |

PLAT-170 は進行中。静的実装・draft PR・CI の成功のみでは Done にしない。
