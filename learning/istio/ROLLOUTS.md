# Rollouts → Istio subset routing（Refs #14）

> Historical learning record. Its manual deployment route is retired for the existing kind.
> Use the [Git/PR → Argo deployment contract](https://github.com/syuhei0519/platform-gitops/blob/main/learning/existing-kind/DEPLOYMENT.md).
> The [original commands at fixed SHA](https://github.com/syuhei0519/application-manifest/blob/01ab7fc8c9c39f204957c6f06d7fc79c400e8b91/learning/istio/ROLLOUTS.md)
> remain available as historical evidence, not as a current execution procedure.


このシナリオは `learning-istio-demo` の **fixture GET `/version`** を対象にする。
既存 Account は mesh に参加させず、同じ代表 CRUD を前後で実行して非影響を確認する。
fixture の割合成功を Account 自体の canary / DB 保全の証拠に置き換えない。
`mesh-demo` の従来手動 VS apply は今回の AC の代替にならない。

## 所有境界

`rollout-chart` は Rollout/Service/VS/DR 各1だけ。すべて `mesh-rollout`。
既存 chart の SA/CM/client をローカル担当が準備し、Core から CM/Deployment を管理しない。
新 Service selector は `app=mesh-rollout`。従来の `app=mesh-demo` と一致せず、Account にも一致しない。
subset routing のため stable/canary Service や Service hash patch は不要。

| 所有者 | 対象 |
| --- | --- |
| Git / Core | Rollout template と10/50/100 step、Service selector/port、VS host/match/route順序/宛先、DR subset順序/基本label |
| Rollouts | VS `/spec/http/0/route/{0,1}/weight`、DR `/spec/subsets/{0,1}/labels/rollouts-pod-template-hash`、status/生成RS/Pod |
| ローカル担当 | 既存 fixture SA/CM/client、namespace revision opt-in、既存 Istio control の復元/撤去、前後の Account CRUD |

Git の VS 初期値は100:0のまま全profile共通。DR は `app=mesh-rollout` と bootstrap の `rollouts-pod-template-hash: unassigned` を持つ。
bootstrap hash はどのPodにも一致させず、初回controller収束で実hashへ置換されるまで受入を開始しない。
Core の `ignoreDifferences` を対象kind/name/namespaceと上記4フィールドに限定し、
`RespectIgnoreDifferences=true` を Application と直接の sync operation の両方に指定する。
route/subset全体や Rollout spec を除外しない。固定配列の順序を変える際は pointer と検証を同時に見直す。
初回createでは ignore は効かないので、VS/DR を100:0で作ってから controller の初期収束を待つ。
固定Argo CDの `restoreNonIgnoredFields` は配列へ再帰しない。desired DRにhashがない旧構成では
subsets全体が「非ignore」と判断されhashが消える。bootstrapを宣言することで正規化前後に差を作りruntime hashを保持する。
route/subsets全体はignoreしない。**同版では配列内の構成driftはOutOfSyncとして検出されてもsyncで自動修復されない**。
各sync前後にCoreのdiff/Syncedと下記helperの完全なVS/DR spec照合を行い、driftがあれば停止してManagerへ返す。
自動修復したと扱わず、手動VS applyで今回のACを置き換えない。
既存アプリに auto sync を追加しない。**有効な手動 GitOps 再同期を各pause/abortで実行して非競合を検証する**。

根拠: [公式subset routing](https://argo-rollouts.readthedocs.io/en/stable/features/traffic-management/istio/)、
[Argo CD の RespectIgnoreDifferences](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/#respect-ignore-differences-configs)。
固定 Rollouts `356117c8e9a21b74b58c2c01d640aa91994522b5` の `rollout/trafficrouting/istio/istio.go` は
VS/DR をget/updateし、DRの基本labelを維持しhashを更新する。専用 controller は `--istio-api-version=v1` を指定する。

## 実適用前の承認対象・不足（Cloudでは適用しない）

platform固定開始 `3f05f580253714d2580e11a6ee75339a469c6df3` の既存 controller は
`learning-services-20261018` だけをwatchする。既存RoleはVS/DR権限を持たず、
Core cacheにもVS/DRがない。既存 controller をclusterwide化しない。
platform提案commit `22331b062b81e181149c7d63838659ea07577565` の `learning/istio/rollouts_access.py` が次の**未適用提案**を描画する。

| 対象 | 必要操作・限定範囲 | 復旧 |
| --- | --- | --- |
| demo専用 controller | `learning-istio-demo` 内に SA/Role/Binding/Deployment 各1、名前 `mesh-rollouts-controller`。既存digest、namespaced/watch固定、v1。requests50m/64Mi、limit500m/256Mi | 所有UID照合後、この4資源だけ解除 |
| demo Rollouts Role | demo内RSの生成/scale/delete、Pod get/list/watch/update、Event write、Lease create/get/update。`mesh-rollout` だけRollout/status/finalizers update/patch、VS/DR update。VS/DR等のinformer readはdemo内 | 同Role/Bindingだけ解除。namespace RBACではRS/Podの所有者別制限はできないため専用namespaceが前提 |
| controller informer read | demo内Deployment/Service/Experiment/AnalysisTemplate/Run/Job/Ingress参照。通知CM/Secret readは固定通知名称だけ。Service patch、AnalysisRun create、Account Secret/DB/PVC writeなし | 同Role。通知Secretを生成/取得する手順はない |
| Core demo Role/Binding | demo内Rollout/Service/VS/DR read/create/update/patch/delete、RS/Pod/Event と既存inclusionに必要なDeployment/AnalysisTemplate/AnalysisRun のget/list/watch。後3kindのwriteなし。対象SAは指定既存control namespaceの `argocd-application-controller` | この2資源だけ解除。既存Account Roleは変更なし |
| Core `argocd-cm` | 既存inclusionsを保持し、in-clusterのVS/DRだけ追加。resourceVersion test付きJSON patch。health/tracking設定は保持 | 最新resourceVersionを確認し、今回追加のentryだけ元に戻す。他変更を巻き戻さない |
| Core登録 | Core Applicationが全体で0件の場合だけ、既存登録の非秘密 `namespaces` 値をシナリオ中 `learning-istio-demo` のみに置換。旧値を私有保存。Accountとのunion登録は禁止 | App/Project削除後、VS/DR inclusionを先に戻し、その後namespace旧値へ戻す。credential/他Secretフィールドを触らない |
| AppProject/Application | 新規 `mesh-rollout`、固定manifest SHA、demo destination、4kindだけ許可。finalizer/prune/auto syncなし | operation終端確認後、orphan削除 |

Coreのnamespace Roleはcreateを名前で制限できない。AppProjectの4kind/destination制限と合わせるが、完全な名前単位security境界とは主張しない。
Core cache inclusionは登録namespaceすべてへ作用するため、Account権限を広げずに共存させることはしない。
登録namespaceをdemoだけにする間は既存CoreにApplicationを追加しない。最初に全Application0件とoperationなしを再確認する。
生成CLIは保存したApplication Listが空でなければ拒否し、非秘密の `registration-namespaces.txt` だけを出す。
demo-onlyへの登録置換・Account namespaceの一時除外と、Core demoのDeployment/AnalysisTemplate/AnalysisRun read追加は**追加承認待ち**。承認前は実適用禁止。generatorはSecret API操作をしない。
`resource.respectRBAC` は既定disabledを保持する。normalは固定Engineでkind全体のmetadataを落とし、
strictは追加SelfSubjectAccessReviewとnamespace横断判定が必要なため採用しない。新Coreも作らない。
この方式は既存Applicationがある場合には利用できず、Managerへ返す。
追加SAの認可、image到達性、controller起動は未実測。`kubectl auth can-i --as ...` は担当にimpersonate権限がある場合だけ使う。

fixture通常最大2 Pod + client の3proxy、既存plain v1も残す場合、fixture requests合計100m/128Mi、
proxy合計300m/384Mi、追加controller50m/64Mi。Istiod/既存Core/他workloadは別に容量を確認する。
DB/PVC/credential・登録のnamespaces以外のSecretフィールド、共有Collector/監視、Kong、mTLS/Retry追加、SLOgateを変更しない。

## Cloudの再現

Helm4.2.4 / Istio1.31.2 / Kubernetes1.36.4、依存digestは既存固定を維持する。
`PLATFORM` は従来validator用の `efd3a1f5a31eaf904bc787456034e4cebe932f7f` を変更なしでcheckoutしたもの。
`ROLLOUTS` は上記固定公式source。`ISTIOCTL` は既存lockとchecksum一致したbinary。

```sh
python3 learning/istio/validate.py --platform "$PLATFORM" --istioctl "$ISTIOCTL" --output "$OUT/static"
python3 learning/istio/validate_rollout.py --platform "$PLATFORM" --rollouts "$ROLLOUTS" --istioctl "$ISTIOCTL" --output "$OUT/rollout"
python3 -m unittest discover -s learning/istio/tests -v
# platform feature checkout内。合成fixtureだけ、実機にapplyしない。
python3 -m unittest discover -s learning/istio -p test_rollouts_access.py -v
python3 learning/istio/rollouts_access.py --control-namespace learning-core-test --owner cloud-fixture --cache "$CACHE" --core-config learning/istio/fixtures/core-config.yaml --applications learning/istio/fixtures/no-applications.yaml --registered-namespaces learning-services-20261018 --output "$OUT/access"
```

`validate_rollout.py` は固定公式Rollout/Istio/Application/AppProject schema、3profile、4kind境界、
無効値拒否、Istio offline analyzeと通常/native injectionを検査する。
injectorのためRolloutと同じPodTemplateを一時Deploymentへ投影するが、この投影を配備しない。
追加の狭い回帰（`ARGOCD` は `e258ee23c3e52266d407572f4bcdfe7d9ed36cb5` 固定source）:

```sh
# application-manifest側。固定公式補正/merge関数をそのまま抽出し、同版依存とK8s threewayapplyを実行。
python3 learning/istio/check_argocd_sync.py --argocd "$ARGOCD"
# platform側。固定EngineのEnsureSynced/IsNamespaced/GetManagedLiveObjsをfakeclientで実行。
python3 learning/istio/check_argocd_cache.py --argocd "$ARGOCD"
```

旧独自syncコピー模型は削除した。これらも実Core/clusterの再同期成功の証明ではない。
既存required/security check名は維持し、renderは既存security scan経路とCI artifactに含める。

## ローカルの一つのscenario（未実施）

独立Review・CI確認後、Managerが上の対象を認可し、local Testが所有UID/context/namespace/容量を確認してから実行する。
全Application0件とdemo-only登録を確認できなければ実測を開始しない。全ての失敗窓は残し、成功するまでのretryをしない。
生成ファイル、raw snapshots、Core patchのresourceVersionは私有一時領域へ置く。公開は集計だけ。

1. mesh OFF状態で既存Account Pod/Serviceとproxy不在を確認し、従来 `account_crud.py` でbaselineを取る。
   DB/PVC/Secret設定は変更しない。Account Serviceの実名/port-forwardは既存READMEの同じ確認手順を使う。
2. 今回commitの既存chartを `plain` でrenderし、所有確認後SA/CM/client/v1だけ準備する。
   このCMには新しい `/readyz` と `measure --rollout` が必要。
   既存platform手順でIstio controlとrevision opt-inを復元し、clientだけrestartしproxyと固定imageIDを確認する。
   新Rollout Podも自動注入を確認する。従来mesh-demo VS/DR/v2が残っていれば所有確認して先に従来手順でplainへ戻す。
3. Coreの全Applicationが0件であることをfresh Listで確認し、登録namespace旧値を私有保存する。
   先に承認済みdemo controller/accessを準備。次に非秘密namespacesをdemoだけへ変更し、最後にVS/DR inclusionを追加する。
   逆順でAccountを登録したままinclusionを足さない。Engine cacheの同期成功を確認する。
   generatorへ実control namespaceと固定manifest commitを渡し、AppProject/Applicationの両方をapplyする。
   合成 `learning-core-test` fixtureを実機へapplyしない。

```sh
: "${ISTIO_CONTEXT:?verified context}"
: "${CONTROL_NAMESPACE:?verified Core namespace}"
: "${MANIFEST_COMMIT:?reviewed fixed SHA}"
: "${PRIVATE_OUT:?private existing output directory}"
k() { kubectl --context "$ISTIO_CONTEXT" -n learning-istio-demo "$@"; }
c() { kubectl --context "$ISTIO_CONTEXT" -n "$CONTROL_NAMESPACE" "$@"; }
r() { kubectl argo rollouts --context "$ISTIO_CONTEXT" -n learning-istio-demo "$@"; }
# 各source変更前にoperationが空で以前のsyncが終端であることを確認する。
source_revision() {
  python3 learning/istio/rollout_application.py --control-namespace "$CONTROL_NAMESPACE" --manifest-commit "$MANIFEST_COMMIT" --revision "$1" --output "$PRIVATE_OUT/app-$1.yaml"
}
sync_demo() {
  c patch application mesh-rollout --type merge -p '{"operation":{"sync":{"prune":false,"syncOptions":["RespectIgnoreDifferences=true"]}}}'
}
measure_demo() { k exec deployment/mesh-demo-client -c fixture -- python3 -B /fixture/demo.py measure "$1" --rollout; }
source_revision v1
sync_demo
```

4. sync `Succeeded` / operation空 / Rollout `Healthy`・stableRS=currentPodHashを待つ。
   初回作成はstepを通過しないので10%成功に数えない。Ready Pod/EndpointSlice、proxy image/revision、XDS ACK/SYNCEDを照合。
   `initial`観測とwarmup+stable200件（v1=200/失敗0）。
5. `source_revision v2; sync_demo`。新hashでstep index1の10%pauseへ。
   VS/DRとclient active routeの `ten` 観測 → **同じv2 Applicationを手動sync** → Succeeded/operation空 → 再観測。
   Coreの同期状態 `Synced`、2出力のhash/weights一致とsource targetRevision/profileを保存。
   非ignoreのdriftでOutOfSyncなら停止。後続syncでの自動修復を前提にしない。routeの伝播が終わる前に測定しない。
   warmup20の後、固定split90窓1000件（v2=60〜140、失敗/未知本文0）。
6. `r promote mesh-rollout`（固定plugin v1.10.1、通常promote、full不可）。
   step index3の `fifty`、同じ手動sync前後の観測、warmup+split50（1000件/v2=440〜560/失敗0）。
7. 同じ通常promoteでstep index5の `hundred`。同じsync前後の観測、warmup+candidate（200件すべてv2）。
   通常promoteで完了し `promoted` 観測。stableRSが旧candidateへ変わり、VSがstable100:canary0でも本文v2になることを確認する。
8. `source_revision failed; sync_demo`。新RS readiness503、stableがReadyで保持されることを確認。
   120秒progress deadlineの自動abortまたは通常 `r abort mesh-rollout` を一度実行。
   追加promote/retryはしない。`aborted` のVS100:0・stable hash保持・Envoy stable100を確認し、
   **failed sourceのままsync**して再観測が一致することを確認する。warmup+candidate200件でstable v2へ到達する。
   同じAccount CRUDを実行し201/200/200/200/204/404を確認する。
9. 終端を確認して既存app出力のv2を再apply/syncしHealthyへ戻す（新しく同名出力を生成して上書きしない）。
   promoted観測とcandidate200件、Account代表CRUDを確認して撤去に進む。

観測はstage毎に次を別ディレクトリへ保存して実行する。
client Podは唯一のReady対象を明示して、`CLIENT_POD` を検証済み実名にする。

```sh
k get rollout mesh-rollout -o json > "$SNAP/rollout.json"
k get virtualservice mesh-rollout -o json > "$SNAP/vs.json"
k get destinationrule mesh-rollout -o json > "$SNAP/dr.json"
"$ISTIOCTL" --context "$ISTIO_CONTEXT" proxy-config routes "$CLIENT_POD" -n learning-istio-demo -o json > "$SNAP/routes.json"
python3 learning/istio/observe_rollout.py --rollout "$SNAP/rollout.json" --vs "$SNAP/vs.json" --dr "$SNAP/dr.json" --routes "$SNAP/routes.json" --stage "$STAGE"
```

helperだけではproxy ACK、Pod readiness/EndpointSlice安定、Core source/operation履歴を証明しない。
local担当はproxy-statusとproxy-config clusters/endpoints、Pod/hash/Ready、sync前後と測定窓の時刻も確認する。
requestはclient Envoy経由・HTTP/1.1 Connection:close・逐次・2秒timeout・50ms待機・retryなし。
窓と許容帯は既存READMEどおり事前固定し、成功要求だけで再正規化しない。100%は200/200を要求する。

## 失敗時・撤去

次のstepへ進まずabort。stable100への復帰を確認してから原因をManagerへ返す。
Controller稼働中の手動VS weight applyを今回の成功証拠にしない。
安定後、Application operation終端を確認してApp/Projectをfinalizerなしでorphan削除し、
Rollout `mesh-rollout` だけforeground削除して所有RS/Pod消失を待つ。VS/DR/Serviceも同名だけ削除。
App/Project削除後、Core inclusionのVS/DR entryを先に戻し、その後登録namespace旧値を復元する。
全Application0件と既存cacheの復帰を確認した後、承認済みdemo access6資源だけを解除する。
既存Account controller、Core/Redis/登録Secret、CA/CRD/namespaceを一括削除しない。
namespace revision opt-in解除と既存plainの再apply、client restart、proxy不在、plain200件/Account CRUDを確認する。
Istio controlの撤去は従来platform手順に従う。安定復帰不能なら独断でSecret/RBAC/DBを変更せずManagerへ返す。
