# Account GitOps / Istio 統合（PLAT-170）

本ディレクトリの実装は [PLAT-169 契約](CONTRACT.md)を Account 専用経路につなぐ。
[明示承認](APPROVAL.md)の範囲で専用kindから10/50/100%・Abort・CRUDを実測した。
再現手順は [SESSION.md](SESSION.md)、実測値と残る統合条件は [PLAT-170-handoff.md](PLAT-170-handoff.md) に記録する。
render/fixtureと実環境証跡を区別し、draft PRのmergeをこの承認に含めない。
固定入力は manifest base `226c9bf68833d3153a78c33f051175918ad7918d`、platform
`867cb81f071d06f7521b93aa110ae001200142a4`、backend `f0425a9c0a70c200fbeb5a549bc8b631bf5a1906`。

## 実装と所有

`runtime_support.py` は固定 manifest SHA と reviewed image catalog を照合して AppProject/Application を生成する。
指定 SHA の Git commit から profile と chart の通常ファイルを読み、同じ内容から migration Job も生成する。
存在しない SHA・image 不一致は停止し、未コミットの作業ツリー変更は実行入力に使わない。
runtime の image は Git に保存する `runtime-profile.yaml` が正本。Application の valuesObject override は使わない。
新旧の切替は profile の Git commit とその固定 SHA に対応し、CLI の一時値を desired version にしない。
`integration.py application` は値と権限を組み立てる offline fixture 用の参照関数。
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
`example.invalid` の fixture digest は runtime pin ではない。proposal 自体は offline 生成物であり、
承認済みセッションでの DB 初期化・migration・Core 登録・実 Pod import は SESSION.md と証跡に分けて記録する。

## image 出所（実 OCI bytes と fresh scan を照合済み）

[image-catalog.pending.json](image-catalog.pending.json) に新旧 manifest/config/source/archive hash と CI 出所を固定した。
旧 source の成功 run は [39086590556](https://github.com/syuhei0519/backend-app/actions/runs/39086590556)。
新 source は [39253620294](https://github.com/syuhei0519/backend-app/actions/runs/39253620294) の
`validate`・`image (backend)`・`ci-result` が成功、publish/propose は cancelled。
`checked-image-1` artifact の OCI archive、manifest/config/全layer digest、source label、公開 scan/SBOM report hash を再照合した。
ローカル旧 Docker config ID とは異なる正規 CI 成果物を採用した。

過去 scan の期限は旧 2026-10-19、新 2026-10-20 で切れていた。pending catalog は `verified=false` の履歴として保持する。
2026-10-22 の新しい DB で両 source/image を既存 Trivy 0.75.0・例外なし policy により再検証し合格。
専用 kind の CRI に OCI digest を保持して import し、新旧 manifest/config の一致を確認した。
現在の採用記録は `image-catalog.reviewed.json`。検証の有効期限を越えて無条件に再利用しない。
実 Pod の imageID も各測定の前後で manifest digest と一致した。
検証済み OCI artifact を専用 node へ offline import した。registry への公開は行っていない。

## 承認後の試験手順と証跡

次は受入の検証契約。実施した正確な順序と採用 revision は SESSION.md、結果は session/evidence.json を参照する。
別セッションには対象操作の承認と image catalog の再検証が必要。merge は今回の承認に含まれない。
全 kubectl は専用 kubeconfig と `--context kind-plat170-account` を指定する。

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
   両 query を同じ `time=T1` で取得。SDK用の追加負荷は測定窓の外に分離し、その receipt も保存する。
7. raw `before/after` と `before_timestamps/after_timestamps`、T0/T1、client receipt の start/end を
   `client_window` に、固定 TARGET、実測 `observed_weights`、新旧実 image を evidence JSON にまとめる。
   `integration.py traffic --evidence ...` は同一系列の counter 差分から新旧分配を判定する。
   10%=1000中60〜140、50%=1000中440〜560、100%=200/200、Abort=stable200/200。
   reset/新規・欠測系列/未知version/非200/不足coverage/古いsample/不一致評価時刻は拒否し、値を補完しない。
8. SDK SLI は `sdk_query_set` に実 stable/candidate hash と Ready instance 数を渡す。
   新旧の5分要求数・error率（1-availability）・250ms以下割合を記録。全 role の5分窓・最低20要求・鮮度を
   [契約](CONTRACT.md)で判定する。100%は candidate のみ、旧版は時刻付き参考値。Pod数を traffic割合に使用しない。
9. 手動の通常 promote で50→100へ進め、同じ手順を繰り返す。`--full` や status 手書きで pauseを飛ばさない。
   100% pause の測定後、同じ candidate に Abort を実行し、stable100%・candidate0%を200件とproxy状態で確認。
   list/create/get/update/delete、削除後404、重複/不正入力の Account API 回帰を固定 backend 契約で実施する。
   Abort は Git 前版復元と区別し、承認されていない Git変更や後続課題の自動開始は行わない。
10. 証跡を保存し、APPROVAL.md の所有照合・cleanup を行う。失敗時も秘密値をログに載せず、残存 resource を記録。

Prometheus instant API の `value[0]` は評価時刻であり、sample鮮度の証明ではない。
同一 query 時刻の [`timestamp()`](https://prometheus.io/docs/prometheus/latest/querying/functions/#timestamp) の値を照合し、
終了側 raw sample が client の終了を覆うことも検証する。

## AC 状態

| AC | 実装と専用環境の実証 | 統合前の扱い |
| --- | --- | --- |
| 1 Git→ArgoCD→Account→Istio | 固定Gitと新旧OCI、fresh scan、実同期・Ready | draft PRの統合待ち |
| 2 実新旧metrics対応 | 実Pod imageID、target_infoのPodUID/source/hash、SDK/Envoy系列 | raw証跡とqueryを保存 |
| 3 10→50→100実要求 | 899/101、485/515、0/200、全エラー0。各5分SDK合格 | Pod比率は使わない |
| 4 Abort/API回帰 | 旧stable200/200、CRUD/409/400/404を含む12要求成功 | Git永続復元・最終昇格とは区別 |
| 5 clean kind再現 | kind/DB/migration/Core/Istio/監視/権限設定と手順、専用kind/kubeconfig削除を実証 | 既存kind ID・default context不変 |
| 6 所有field/reconcile | 各pauseで通常sync2回、4 pointer/実ルート/Pod identity保持 | ignore範囲を拡大しない |

PLAT-170 は進行中。静的実装・draft PR・CI の成功のみでは Done にしない。

## 構成と入口

配備方式は [RELEASE-MODES.md](RELEASE-MODES.md)、操作・状態の約束は [CONTRACT.md](CONTRACT.md) から読みます。

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [application-chart/](application-chart) | Account リリースを管理する Argo CD Applicationを描画する Chart。 |
| [backstage/](backstage/README.md) | リリース操作の画面と、認証・権限を確認して共通処理を呼ぶ backend。 |
| [chart/](chart) | Account の段階配備と解析を描画する Chart。 |
| [fixtures/](fixtures) | 状態遷移・観測結果・権限の境界を再現する固定した試験入力。 |
| [metric-plugin/](metric-plugin/README.md) | Argo Rollouts から呼ばれ、計測対象の版と結果を検証する Go plugin。 |
| [session/](session) | 過去の承認済み試行で使った補助コードと結果。現在の通常配備入口とは分けて読みます。 |

### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [chart/templates/release.yaml](chart/templates/release.yaml) | Account の Rollout と Service、Istio の経路を同じ設定から出力します。 |
| [chart/values.schema.json](chart/values.schema.json) | Account の段階配備と解析に渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [contract.py](contract.py) | Account リリースの状態と要求を検証する共通規則です。UI や実環境への操作から切り離して使います。 試験クライアントの ConfigMap に原文が埋め込まれます。 |
| [deployment_target.py](deployment_target.py) | リリース操作が触れてよい対象と取得元を定義し、任意の対象へ操作が広がるのを防ぎます。 試験クライアントの ConfigMap に原文が埋め込まれます。 |
| [envoy-scrape.yaml](envoy-scrape.yaml) | Account の段階配備を比較するため、Envoy の観測データを収集する設定です。 |
| [image-catalog.existing-kind.json](image-catalog.existing-kind.json) | 既存環境向けに参照するイメージ候補と固定 digest の対応表です。 |
| [image-catalog.pending.json](image-catalog.pending.json) | レビューが済む前のイメージ候補を保持し、確認済み候補と区別する一覧です。 |
| [image-catalog.reviewed.json](image-catalog.reviewed.json) | レビューしたイメージ候補と由来を、配備提案で照合するための一覧です。 |
| [load.py](load.py) | 承認した試験クライアントから固定の読み取り先へ要求を送り、配備比較用の小さな負荷を作ります。 試験クライアントの ConfigMap に原文が埋め込まれます。 |
| [policy-cases.json](policy-cases.json) | リリース方針の許可・拒否を静的検証するための入力ケースです。 |
| [runtime-profile.yaml](runtime-profile.yaml) | Account の配備版と配備方式を指定する入力です。リリース用 Chart と操作処理が参照します。 |
