# 学習専用: Account / Order → Payment / Notification

> Historical learning record. Its manual deployment route is retired for the existing kind.
> Use the [Git/PR → Argo deployment contract](https://github.com/syuhei0519/platform-gitops/blob/main/learning/existing-kind/DEPLOYMENT.md).
> The [original commands at fixed SHA](https://github.com/syuhei0519/application-manifest/blob/01ab7fc8c9c39f204957c6f06d7fc79c400e8b91/learning/README.md)
> remain available as historical evidence, not as a current execution procedure.


既存の `charts/`、GitOps observer、正式release validatorとは独立した手動配備経路です。
1サービスごとにAPI Deployment/ClusterIP Service、専用PostgreSQL StatefulSet/Service、
PVC、初期化ConfigMap、migration Jobを描画します。まずAccount/Orderを受け入れ、
その後Payment、Notificationを個別に追加します。Argo、監視、Kafka、Sagaは含みません。
正式公開情報の省略はこの経路のみです。既存Lab・元本DB/PVCは使用しません。

現時点は設定実装のみ。image build/配送/load、実配備、API・restart/PVC受入は未検証です。
Issue別の担当範囲は [acceptance.json](acceptance.json)。過去の試行・失敗・上限を保持し、
この経路の追加を正式releaseの再試行予算リセットに使いません。

## Cloudの入力と固定成果物

実装・Unit/API/DB検証・image build・描画はCloud。ローカルPCは承認済みimageのimportと
実Kubernetes操作・実測のみを担当します。Dockerfile記載の旧ローカルbuild手順もCloudで実行します。

| サービス | backend-app source候補（40桁） | Dockerfile | DB / role |
| --- | --- | --- | --- |
| account | `d48a97851619912e7efc80c470912907ce965dc7` | `Dockerfile` | account / account_app |
| order | `d48a97851619912e7efc80c470912907ce965dc7` | `Dockerfile.order` | orders / order_app |
| payment | `bf3d1ab34b4553896fdb0b8d7acfc75185b77757` | `Dockerfile.payment` | payments / payment_app |
| notification | `7cdbab9bae115c495053e247e6a84a277b9d52cc` | `Dockerfile.notification` | notifications / notification_app |

Payment PR48 / Notification PR49はこの入力時点で未マージの独立候補です。mainにあると仮定したり、
両branchをマージしたりしません。後続build担当は採用許可された各固定sourceを別々に取得し、
clean checkout・実在するDockerfile・platform (`linux/amd64`) を照合します。
採用sourceの変更は `sourceCommits` を明示更新し、build/描画/独立検証をそのpinで取り直します。
固定入力からのbuild手順であり、再buildの出力bytes一致を実測したという主張ではありません。

後続の承認済みCloud buildでのみ、各sourceに対応する上表のDockerfileを使用します:

```bash
set -euo pipefail
# SOURCE / SERVICE / DOCKERFILE / OUTは承認済み入力。OUTはcheckout外。
test "$(git rev-parse HEAD)" = "$SOURCE"
test -z "$(git status --porcelain)"
docker build --platform linux/amd64 -f "$DOCKERFILE" \
  --build-arg VCS_REF="$SOURCE" \
  --build-arg VCS_SOURCE=https://github.com/syuhei0519/backend-app \
  --iidfile "$OUT/image.id" -t "$SERVICE-learning:build" .
IMAGE_ID=$(cat "$OUT/image.id")
test "$(docker image inspect "$SERVICE-learning:build" --format '{{.Id}}')" = "$IMAGE_ID"
IMAGE="local.learning/$SERVICE:$SOURCE-${IMAGE_ID#sha256:}"
docker tag "$IMAGE_ID" "$IMAGE"
docker image save -o "$OUT/$SERVICE.docker.tar" "$IMAGE"
(cd "$OUT" && sha256sum "$SERVICE.docker.tar" > "$SERVICE.docker.tar.sha256")
```

PostgreSQLも既存pin `postgres:17.10-bookworm@sha256:9b18b78397054fce88a9552e9d5a3ad5bb7fd258c5b3cc1c5028e46373d6ea8f`
の承認済みlinux/amd64 imageをCloudで用意し、upstream digestとの対応を照合して
`local.learning/postgresql:<config-IDの64桁hex>` へtagし、同じ方法でDocker archiveを保存します。
各archiveの全bytes SHA-256、サイズ、実source/Dockerfile hash、platform、image名、config ID、
取得可能なmanifest digestとその取得方法を非秘密のbuild記録へ保存します。
archive SHA、manifest digest、config IDは別物です。未公開imageのRepoDigestが空なら理由付き未取得とし、捏造しません。
OCI出力の場合はCloudで承認済みtoolによりDocker archiveへ変換し、変換前後のidentityと最終bytesを記録します。

最小配送候補は、既存ChatGPT Libraryへarchive・チェックサム・build記録を保存し、所有者がPCへ
ダウンロードして全bytesを照合する方法です。Libraryスキルの大容量/複数ファイル用経路が利用可能です。
実サイズでの保存・取得・同一性はbuild工程で検証し、不可ならdelivery blockedを返します。
GitHub連携にはActions成果物の一覧/ZIPダウンロード機能もありますが、これは任意のCloud shellから
Actions artifactをuploadできるという意味ではありません。既存の承認済みActions生成経路がある場合のみ代替できます。
GHCR公開、新規credential、新規課金サービスは前提にしません。今回は配送を実行していません。

CloudでHelm 4.2.4 / Python3と `requirements.txt` の依存を使って静的検証します:

```bash
python3 learning/validate.py
python3 learning/check_build_failfast.py
```

このfixtureは架空image IDの描画だけです。実image取得の根拠にはしません。
実行用の非秘密valuesは各サービス別にcheckout外で保存します（Secret値は一切含めません）:

```yaml
service: account # order / payment / notification は別々に描画
owner: <承認された専用所有ラベル>
imageConfigID: sha256:<このサービスの実config-ID 64桁hex>
database:
  imageConfigID: sha256:<承認済みPostgreSQLの実config-ID 64桁hex>
  storageClass: standard # 実targetのlocal-path provisionerのclass名を確認して指定
```

`NAMESPACE` もCloudの非公開入力で明示し、`learning-*` の新規専用namespaceだけを使用します。
全サービスを同じnamespaceへ置き、1サービスずつ以下を描画します。変数に既定targetはありません。

```bash
helm template "learning-$SERVICE" learning/chart --namespace "$NAMESPACE" \
  --kube-version 1.36.4 -f "$VALUES" --output-dir "$BUNDLE/$SERVICE"
```

同じ固定Manifest commitから作った全renderファイル・非秘密values・inventory・その全bytes hashを
Cloudで保存し、承認された配送に含めます。ローカルは再build/再描画せず、この固定bundleを適用します。
実Manifest commitとartifact SHAを独立した承認入力にpinし、ダウンロードしたchecksumだけを信頼の起点にしません。

## ローカルPC: 対象確認 → import → 段階apply

以下は後続の承認済みローカル担当専用です。Cloudでは実行しません。
CONTEXT/CLUSTER/NAMESPACE/OWNER/EXPECTED_API_SERVER/STORAGE_CLASS/BUNDLEはManagerが確認した正確な非公開入力を渡します。
現在選択されたkind/contextは非公開Handoffに保持し、public runbookへ実環境識別子を転記しません。
既存contextを切り替える `use-context`、kubeconfig絶対pathの埋込み、cluster作成/削除は不要です。

```bash
set -euo pipefail
: "${CONTEXT:?}" "${CLUSTER:?}" "${NAMESPACE:?}" "${OWNER:?}" "${EXPECTED_API_SERVER:?}" "${STORAGE_CLASS:?}" "${BUNDLE:?}"
test "$CONTEXT" = "kind-$CLUSTER"
[[ "$NAMESPACE" =~ ^learning-[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]
test "$(kubectl --context="$CONTEXT" config view --minify -o jsonpath='{.contexts[0].context.cluster}')" = "kind-$CLUSTER"
test "$(kubectl --context="$CONTEXT" config view --minify -o jsonpath='{.clusters[0].cluster.server}')" = "$EXPECTED_API_SERVER"
k() { kubectl --context="$CONTEXT" --namespace="$NAMESPACE" "$@"; }
k get nodes -o wide
k get storageclass "$STORAGE_CLASS" -o yaml
```

接続先、Kubernetes実version、linux/amd64 node、storageClassのlocal-path provisionerとrender内class名の一致、許可範囲、他作業との分離を
read-onlyで照合します。namespaceが存在すれば新規用として再利用せず停止します。
新規namespaceの作成が許可されていることを確認してから:

```bash
EXISTING_NAMESPACE=$(k get namespace "$NAMESPACE" --ignore-not-found -o name)
test -z "$EXISTING_NAMESPACE"
k create namespace "$NAMESPACE"
k label namespace "$NAMESPACE" learning.local/owner="$OWNER" app.kubernetes.io/part-of=learning-only
test "$(k get namespace "$NAMESPACE" -o jsonpath='{.metadata.labels.learning\.local/owner}')" = "$OWNER"
```

作成応答が不明なら同名を再作成せず照合します。今回作成できたnamespaceと各資源のUID/nameを
private inventoryへ記録します。既存PVCやDBを借りません。各サービス専用Secret `learning-<service>-db`
の `admin-password` / `app-password` は所有者が承認済みprivate手段で実行時に供給します。
ChartはSecretを生成しません。資格情報の生成、Git/values/render/bundle/logへの保存は禁止です。

approved checksumとbuild記録を先に照合し、各appとPostgreSQL archiveについて:

```bash
sha256sum -c "$ARCHIVE_CHECKSUM_FILE"
docker load --input "$ARCHIVE"
test "$(docker image inspect "$IMAGE" --format '{{.Id}}')" = "$EXPECTED_IMAGE_CONFIG_ID"
test "$(docker image inspect "$IMAGE" --format '{{.Os}}/{{.Architecture}}')" = linux/amd64
kind load docker-image "$IMAGE" --name "$CLUSTER"
```

tag名の一致だけでは受け入れません。各kind nodeの `crictl inspecti "$IMAGE"` でrepoTags・id・repoDigestsを
確認し、approved archive/config ID/manifestとの対応を記録します。必要なnode runtime IDの形式が異なる場合は
そのmanifest/config対応を照合し、文字列を勝手に置き換えません。不明ならapplyを停止します。
Pod起動後も `status.containerStatuses[].imageID` と使用imageの対応を保存します。pullは全workloadでNeverです。

Account、Orderの順で下記を実行し、受入後にPayment、Notificationをそれぞれ追加します。
`SERVICE` はその1サービス、`D` はCloudで描画した対応directory。bundle hash/namespace/owner/inventoryを
確認し、所有外の同名資源があれば停止します。APIを先に適用しません。

```bash
D="$BUNDLE/$SERVICE/learning-service/templates"
k rollout status "statefulset/learning-$SERVICE-db" --timeout=300s
k wait --for=condition=complete "job/learning-$SERVICE-migration" --timeout=200s
k rollout status "deployment/learning-$SERVICE" --timeout=300s
```

migration失敗/timeoutではここで停止し、APIを開始しません。Job backoffは0です。
再試行は既存budget/承認内だけ。原因・失敗証跡を保存し、所有する失敗Jobだけを削除して同じpinで再実行します。
新image/configへの更新時は所有APIを停止してから新migrationを待ちます。既存完了Jobの再applyを新migration成功と扱いません。
DB初期化scriptは空PVCで一度だけ動きます。Secret更新だけで既存DBのpasswordは変わりません。
forward-only migrationの履歴編集、失敗後のdown migration、自動DB削除は行いません。

## 必要な実測とcleanup

Service DNSとprobesは実clusterで確認します。`k port-forward --address 127.0.0.1 service/learning-<service> <private-port>:8080`
を使い、固定sourceのAPI契約に従って試験データだけで [acceptance.json](acceptance.json) の最小ケースを測定します。
Orderは作成/取得・代表不正入力4xx、Accountは作成/取得/更新/削除。Paymentはsuccess/decline・同じrequest_id再送・GET、
Notificationは受付/GET・同じrequest_id再送とreceipt重複抑止。Order参照は歴史的IDでありサービス間callを意味しません。
各APIの `k rollout restart deployment/learning-<service>` 後に同じid/内容を取得し、既存Account/Order smokeを維持します。
DB/PVCは各サービス所有を再照合し、必要なPVC試験は当該DB Pod再作成後の同一PVC/既存行取得に限定します。
手元のGo/Unit/API suiteの再実行や外部決済・メール送信は追加しません。

private実行記録へcode/Manifest SHA、values/render/archive hash、image/tag/config ID/node runtime ID/Pod imageID対応、
context/cluster/endpoint/namespace/所有UID、時刻・コマンド・結果・migration/probes/DNS/restart/PVC・有限retry・cleanupを保存します。
再生成した文章でraw証跡を代替せず、固定bytesとhashでManagerへ配送します。公開版にはSecret/接続情報/実環境IDを含めません。

cleanupは証跡配送後、削除が許可された今回の専用namespace内のinventoryだけ。通常はAPI/Jobを止めDB/PVCを保持します。
完全破棄が承認された場合のみ、namespace owner/UIDとprivate inventoryの一致・予期しない資源がないことを確認し、
`k delete -f "$D/api.yaml" -f "$D/migration.yaml" --ignore-not-found`、続いて当該 `database.yaml` を削除します。
database.yaml削除は当該PVCも削除し、local-pathのreclaimによりデータを失うことを明示して承認範囲と照合します。
Secretとnamespaceも今回所有分だけ個別削除します。cluster、node、PV一括削除、他namespace、既存Labは対象外です。
cleanupが失敗/不明ならその対象と結果を記録して停止し、再作成やwildcard削除で回避しません。

## Account telemetry opt-in

`learning/chart`の`telemetry.enabled`は既定`false`。既定offの全サービスrenderは変更しない。
Accountに限り`true`を指定でき、Order/Payment/Notificationのtrueは拒否する。
監視側はplatform-gitopsの独立`learning/monitoring/chart`を使用する。
既存DB/Secret/PVC/imageは変更せず、backendの再buildもこのopt-inに含めない。

```yaml
telemetry:
  enabled: true
```

有効時は既存SDK用の固定Collector endpoint、environment=local、`POD_NAMESPACE=account`、
実Pod UID（Downward API）の4入力だけをAPI専用helperから追加する。
migration Jobには追加せず、完了済みJobのimmutable PodTemplateを変更しない。
`POD_NAMESPACE=account`はlegacy SDK互換値で、実namespaceではない。
専用CollectorでAccount/local/互換namespaceをfilterしてから、承認済み実`learning-*` namespaceへ
resource属性を補正する。実namespaceを監視valuesに明示し、互換値を実namespaceとして表示しない。
この属性検査は送信者認証ではない。実SDK/環境でnamespace制約がセキュリティ境界として使われている場合は
互換値で迂回せずManagerへ報告する。

送信対象imageに既存SDKと40桁buildCommitが入っていること、固定endpointへのService DNS/配線、
実namespace/Pod UID/source/image identityを後続の承認済みkind受入で確認する。
起動検査不一致、同名Collectorとの衝突、既存権限不足は停止。匿名Viewerのlive適用も別承認が必要。
Cloudの`python3 learning/validate.py`はschema/renderの確認だけでlive AC合格を意味しない。
正常200と安全な400、5m窓経過後のidle、所有Collector停止/復旧によるmissingの手順は
[監視runbook](https://github.com/syuhei0519/platform-gitops/tree/main/learning/monitoring)を参照する。
監視opt-inでは**Account API Deploymentだけ**を配送・適用・復旧対象にする。
全chart renderや`api.yaml` template全体（Serviceも含む）はapplyしない。
Jobの削除・再実行、DB/StatefulSet/PVC/Secret/ConfigMap/Serviceの適用は対象外。
既存の固定済み完全values（telemetry.enabled=false、同じsource/image/DB/owner等）から次を実行する。
既存設定を変更せず、telemetryの4入力だけを差分として検査する。cluster操作は行わない。

```sh
python3 learning/validate.py --account-telemetry-only \
  --account-values existing-account-values.yaml --namespace "$ACCOUNT_NAMESPACE" \
  --output account-telemetry-delivery
```

新規出力directoryには`account-api-before.yaml`（復旧用）、`account-api-telemetry.yaml`（適用用）、
`inventory.json`（1 Deploymentのnamespace/name/ownerと両ファイルのsize/SHA256）だけが入る。
両YAMLをparseし、inventoryが実Account Deploymentと一致することを確認してbytesを固定・配送する。
実AccountとbeforeのPodTemplateが一致しなければ停止し、既存値を取得し直して別の固定成果物を作る。
以前の成果物を上書きしない。合成fixtureによる出力を実対象の適用ファイルに流用しない。
Managerが対象/操作を承認した後のみ`account-api-telemetry.yaml`をapplyし、Account rolloutを確認する。
復旧も保存済み`account-api-before.yaml`だけをapplyする。両操作ともinventoryは
`apps/v1 Deployment learning-account`の1件だけで、完了済みmigration Jobには触れない。

## Order → Payment の任意配線（静的検証のみ）

`orderPaymentURL` は既定 `''`（未設定も同じ）です。無効時は従来の描画を維持し、
`ORDER_PAYMENT_URL` 自体を出力しません。有効化は `service: order` のAPIだけに限定します。

```yaml
service: order
orderPaymentURL: http://learning-payment:8080
```

基点 `9dfe26057cd2f3404278dd0dca614a7d68c11352` の `templates/_helpers.tpl` は
`learning-<service>` を生成し、`templates/api.yaml` のServiceは `.Release.Namespace`、
port `8080` / targetPort `http`、API containerPort `8080` を使用します。
上記の相対名は、既存手順どおりPaymentとOrderを**同じ、明示された `learning-*` namespace**へ
描画する場合のoriginです。固定された実namespace名はchartにありません。
実namespace/DNS/ready EndpointSliceは後続LocalTestで照合し、推測で補いません。
URLはこのoriginまたは空文字だけをschemaで許可し、他サービスの非空値、別host、path、認証情報付きURLは拒否します。

対象backendは独立検証済みPR51 head `92a337ca50b310bf7895116b035618b2fe98bca6` です。
[固定通信仕様](https://github.com/syuhei0519/backend-app/blob/92a337ca50b310bf7895116b035618b2fe98bca6/docs/order-payment-communication.md)
に従い `POST /api/orders/{id}/payments`、既存Paymentのrequest_id重複防止、
downstream 2秒 / route 5秒、1 POST・自動retryなしを利用します。成功/拒否/replay/conflictを区別し、
応答喪失はunknown outcomeとして同じrequest_id・同じ入力で手動回復します。
この配線はsource/image pinを更新しないため、後続担当は承認済みOrder imageがこのbackend headに
対応することを確認してください。既存imageのまま設定しても新API実装の証明にはなりません。

```bash
python3 learning/validate.py --order-payment-only
```

既存fixtureで未設定=空、APIのenv 1件だけの差分、migrationのraw bytes不変、
DB/PVC/Service等の非API資源不変、他サービスと不正値の拒否を検証します。
Helm renderは実Service DNS疎通の合格ではありません。

有効化・無効化・復旧は後続承認済みLocalTestだけで行います。実values/image/namespaceを固定し、
既存手順でoff/onを別々に描画して、差分がOrder Deploymentの当該envだけであることを確認します。
適用対象はOrder Deploymentのみで、Service、完了migration Job、DB、Secret、PVCへ再適用しません。
無効化は `orderPaymentURL: ''` として当該envを除きます。復旧は保存済みの変更前Order Deployment
（image/configを含む）を復元し、rollout/readinessを確認します。DB rollbackや新しいstateは不要です。

未検証: 実imageの対応、実namespaceでのPayment Service/EndpointSlice/DNS、Order Service経由の
成功・拒否・同一request_id replay/conflict・timeout/応答喪失、実適用と復旧。
独立Test/Review、同一headのCI、Managerの製品AC最終判断は別工程です。

## 構成と入口

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [account-release/](account-release/README.md) | Account の配備提案、操作・観測の検証、Backstage と計測 plugin。 |
| [canary/](canary/README.md) | Canary 配備の成功・失敗・計測条件を検証する定義と試験。 |
| [chart/](chart) | 学習用の各 API と専用 DBを描画する Chart。 |
| [existing-kind/](existing-kind/README.md) | 既存環境の所有範囲と GitOps 入口、引き継ぎ時の提案・記録。 |
| [istio/](istio/README.md) | 通信経路、sidecar、段階配備を学ぶための定義と静的検証。 |

### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [chart/values.schema.json](chart/values.schema.json) | 学習用の各 API と専用 DBに渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [chart/values.yaml](chart/values.yaml) | 学習用の各 API と専用 DBを描画する既定値です。環境別の値は呼び出し側から上書きします。 |
