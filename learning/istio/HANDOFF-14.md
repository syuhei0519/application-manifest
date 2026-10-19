# Handoff

## 目的

Refs #14。主変更は専用fixtureの Rollout step→Istio subset weight と GitOps/runtime の所有境界。
ACは10/50/100%反映、再同期でweight/hash非競合、abortでstableへ戻り既存Account代表CRUD成功。
**実cluster ACは未完了**。実装/Cloud検証だけを担当し、独立Review・現在CI・merge・local TestはManager側へ渡す。

- System正本: `syuhei0519/ai-development-system@42521c0bd2a1220ef01ed009455a9372708f8314` の `agents/implementation.md`、`workflows/single-issue.md`、`templates/handoff.md` を確認。checkout `.agents/skills` はなし。
- application-manifest 開始main: `de2d6238d51674998fdf94436211dc8ba38ee843`、branch `feat/istio-rollouts-14`。
- application-manifest 実装固定commit: `6ea80d453a63040624166877a205a46ad7b25019`。このHandoffのcommitは後続の記録commit。
- platform-gitops 開始main: `3f05f580253714d2580e11a6ee75339a469c6df3`、branch `feat/istio-rollouts-14`、実装固定commit `08faffc86ac0b43fcd5c7bb8149709040b5613ad`。
- platform Draft PR: https://github.com/syuhei0519/platform-gitops/pull/97 。主repoのDraft PRはこのbranchのPRを参照。
- backend固定開始指定は `76273c5201725cd16cdf4500fcccb77c373ebcf7`。backendソース/trace/Collectorは担当外で変更なし。

## やったこと

- `rollout-chart` に専用Rollout/Service/VS/DR各1。10/50/100 + 明示pause、subset stable/canary、同じ固定Python imageを使用。既存DB/Secret不要。
- `rollout_application.py` にCore AppProject/Application生成。固定SHA・専用namespace・4kind限定。VS2 weight/DR2 hashだけignoreし、RespectIgnoreDifferencesを有効化。
- `observe_rollout.py` が保存JSONのRollout世代/pause/hash、VS/DR、client Envoy active routeを照合。未知/誤経路、stale statusを拒否。
- 既存fixtureへ `/readyz` と失敗revision、rollout宛先選択、100% v2固定窓を追加。従来default/90:10/50:50/plainの設定と固定digestは保持。
- platform `learning/istio/rollouts_access.py` にdemo専用namespaced controllerと限定Role/Bindingの未適用提案、Core inclusionsだけのresourceVersion付きpatch。
  既存Account controller/watch/health/Roleを変更しない。専用controller自身はsidecar opt-out。
- 両repoの既存CI jobへ必要な検証/render/artifactを結線。required/security check名、Kubernetes1.36.4の描画条件、Helm4.2.4/Istio1.31.2の固定を保持。
- [ROLLOUTS.md](ROLLOUTS.md) に1 scenarioの同期/10→50→100→promotion→failed/abort→復旧、権限と解除範囲を記載。fixture routingと実Account CRUDの責務を分離。

## 観察結果

### Cloudコマンドと結果

Python依存は既存 `learning/requirements.txt`、tool/imageは既存固定値。シェルのPATHに検証済みHelm4.2.4を置く。
`OUT` は未作成の子ディレクトリを作れる私有一時領域。上書きで過去失敗を消さない。
platformの変更用checkoutと、従来validator用固定sourceは区別する。

```sh
# application-manifest 実装commitのcheckout内
python3 -m pip install -r learning/requirements.txt
# PLATFORM: platform-gitops@efd3a1f5a31eaf904bc787456034e4cebe932f7f の変更なしsource
# ROLLOUTS: argoproj/argo-rollouts@356117c8e9a21b74b58c2c01d640aa91994522b5
# ISTIOCTL: platform既存versions.lock.jsonとchecksum一致する1.31.2 binary
python3 learning/istio/validate.py --platform "$PLATFORM" --istioctl "$ISTIOCTL" --output "$OUT/static"
python3 learning/istio/validate_rollout.py --platform "$PLATFORM" --rollouts "$ROLLOUTS" --istioctl "$ISTIOCTL" --output "$OUT/rollout"
python3 -m unittest discover -s learning/istio/tests -v
# platform-gitops 実装commitのcheckout内
python3 -m unittest discover -s learning/istio -p test_rollouts_access.py -v
python3 learning/istio/rollouts_access.py --control-namespace learning-core-test --owner cloud-fixture --cache "$CACHE" --core-config learning/istio/fixtures/core-config.yaml --output "$OUT/access"
```

| 検査 | 結果 |
| --- | --- |
| 従来4profile lint/render、Istio公式v1 schema、default復帰、20負例 | PASS |
| 新3profile、固定公式Rollout/Istio/Application/AppProject schema、4資源境界、4無効入力 | PASS |
| 新旧PodTemplateのoffline analyze/通常・native injection、固定proxy/fixture digest | PASS。Rolloutは一時Deploymentに同一templateを投影して注入検査、配備しない |
| fixture/観測/所有境界 unit | 10 tests PASS。loopbackの失敗readiness503/livez200、stale/step/hash/VS/Envoy/GET不一致拒否を含む |
| platform実効権限/拒否/既存Core設定保持 | 4 tests PASS、合成Core入力でCLI描画PASS |
| 既存固定scan-gateのrender構造検査 | manifestの `ba8b3d211ac3e497900abd6c6f44f45388e3801c`、platformの `96e5509757fbfb4bdc7e8b47876cdcc8c288142c` で各PASS |
| kubeconform strict 1.36.0（既存CI schema条件） | platform6 valid/0 invalid/0 error。manifest3 valid/15 CR skip/0 error。CRは上記固定公式schemaで別途検査 |
| `git diff --check` / Python compile | PASS |

必要なscan-gateは上記backend固定commitの `tools/scan-gate` をGit archiveで一時領域に展開し、
`go build -buildvcs=false -o "$GATE" .` の後、`"$GATE" -check-render-only "$RENDER" -out "$RESULT"` で再現できる。
kubeconformは既存CIの固定image/digest/strict/1.36.0を使う。Cloudコンテナからのschema取得はTLS信頼で失敗したため、
hostのTLS検証付きcurlで同じv1.36.0-standalone-strictの5種schemaを取得し、read-only mount + `--network none` と
`-schema-location '/schemas/{{.ResourceKind}}{{.KindSuffix}}.json'` で再実行し成功。TLS検証無効化はしていない。

### 失敗履歴・未検証

- `bash ci/github/test.sh` はdocs回帰14caseとinventory Goテスト成功後、未materializeの `tools/scan-gate` 不在で停止。
  全CI成功と扱わない。必要なrender guardだけ固定sourceからbuildして実行した。残りの全機能回帰は実行していない。
- Git archiveしたguardの初回buildはVCS stamping取得エラーで失敗。sourceを変えず `-buildvcs=false` に限定してbuildを成功させた。
- kubeconform初回はコンテナのschema取得がTLS CAエラーで6 errors。上記の同じschemaのhost取得・networkなし再実行で6 valid。
- source照合でRollouts1.10.1の `status.observedGeneration` が文字列であることを確認し、比較と合成fixtureを修正済み。
- 実controller起動/SA認可/Ready/image到達性、10/50/100の実route/要求先、Coreの再同期前後、abort、cluster復帰後Account CRUDはすべて未実測。
- unitのsync prepatchモデルはArgo CD runtimeの証明ではない。offline injectionはRollout自動注入のruntime証明ではない。
- source vuln/secret/misconfigの完全なCI、独立Review、mergeは担当外。新規image公開/正式Order GitOps#60/共有監視は未実施。

## 学び

subset routingならService selectorをcontrollerに更新させず、weightとhashの4フィールドを切り分けられる。
ただしArgo diff ignoreだけではapply時の上書きを防げず、sync optionと実際の再同期の確認が必要。
namespace限定controllerに他namespaceのRBACだけ足しても監視範囲は広がらない。

**次の担当**: Managerが固定差分の独立Reviewと現在CIを確認し、必要なmergeを別担当へ委任。
local Testは [ROLLOUTS.md の承認対象表](ROLLOUTS.md#実適用前の承認対象不足cloudでは適用しない) を確認し、認可後に1 scenarioで実測する。
既存Core登録のnamespacesにdemoがないため、登録方式/許可が決まるまではruntime開始を保留。
この登録はSecretに関わるが、現指示はSecret変更禁止。実装は登録Secretを生成/取得/変更していない。
状態は「未実施・許可待ち」で、拒否された操作や不明な実行結果はない。

demo access6資源、Core inclusion追加entry、App/Project、Rollout/生成RS/Pod/同名Service/VS/DRが今回の解除範囲。
Account controller/DB/PVC/Secret・既存Core設定全体を巻き戻さない。namespace/CRD/CAを削除しない。
独立Review実施済みとは主張せず、runtime ACが揃うまではIssueをCloseしない。
