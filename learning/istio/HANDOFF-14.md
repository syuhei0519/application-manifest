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

### 独立Review後のblocking 3件の修正（2026-10-19追記）

上記は初回提出時の履歴。独立Reviewでcache権限、DR hash削除、Envoy route遮蔽の3件がblockingと判明した。
初回の独自syncコピー模型のPASSは固定Argo実装の証明にならず、模型を削除して下記へ置換した。
初回の成功記録や失敗履歴は消さず、この追記で現行の結論を更新する。

- manifest修正固定commit: `e7328f2a3530e948dd590b7c396027a624056a98`。
- platform修正固定commit: `22331b062b81e181149c7d63838659ea07577565`。
- 同じDraft PR: [manifest #40](https://github.com/syuhei0519/application-manifest/pull/40)、[platform #97](https://github.com/syuhei0519/platform-gitops/pull/97)。この記録の後続commitはMarkdownだけ。

1. **cache**: inclusionは登録全namespaceへ作用するため、Account namespaceとdemoのunion登録をやめる。
   全Core Applicationが0件であるfresh Listをgeneratorへ渡し、シナリオ中だけ非秘密`namespaces`をdemoのみに限定する。
   demo Core Roleへ既存inclusionに必要なDeployment/AnalysisTemplate/AnalysisRunのget/list/watchだけを追加。
   Account Roleは変更せず、新Core/credential/SSAR権限を追加しない。既定respectRBAC=disabled以外の設定はgeneratorで拒否する。
   normalは固定Engineで共有kind metadataを失うため不採用。strictはSSARとnamespace横断判定を要するため不採用。
2. **hash**: desired DRに `rollouts-pod-template-hash: unassigned` を宣言し、固定版補正が配列を丸ごと元に戻さないようにする。
   ignoreは従来の4フィールドだけ。固定公式補正関数/getMergePatch/applyMergePatchをGit sourceからそのまま抽出して検証する。
   **同版では配列内の非ignore driftは検出されても自動修復されない**。OutOfSyncとVS/DR完全spec照合で停止し、成功と数えない。
3. **Envoy**: 最初にGET `/version`へ適用されるrouteを評価する。先頭prefix `/`等の遮蔽を拒否し、
   判定できない先行matcher/headerもfail closed。明らかに不一致の先行path/prefix/POSTだけを読み飛ばす。

狭い再現コマンド（`ARGOCD` は `argoproj/argo-cd@e258ee23c3e52266d407572f4bcdfe7d9ed36cb5` 固定checkout）:

```sh
# manifest側。現在のchartを描画し、固定版go.mod/go.sumと公式関数+K8s threewayapplyを実行。
python3 learning/istio/check_argocd_sync.py --argocd "$ARGOCD"
python3 -m unittest discover -s learning/istio/tests -v
python3 learning/istio/validate_rollout.py --platform "$PLATFORM" --rollouts "$ROLLOUTS" --istioctl "$ISTIOCTL" --output "$OUT/review-rollout"
# platform側。固定Engineを一時archiveに展開し、実際の宣言Roleをfakeclientに与える。
python3 learning/istio/check_argocd_cache.py --argocd "$ARGOCD"
python3 -m unittest discover -s learning/istio -p test_rollouts_access.py -v
python3 learning/istio/rollouts_access.py --control-namespace learning-core-test --owner cloud-fixture --cache "$CACHE" --core-config learning/istio/fixtures/core-config.yaml --applications learning/istio/fixtures/no-applications.yaml --registered-namespaces learning-services-20261018 --output "$OUT/review-access"
```

結果:

- 固定Engine回帰3 case PASS。旧union+disabledはcache同期失敗を再現。normalは同期してもIstio kind metadataを失うことを再現。
  demo-only+disabledはEnsureSynced、IsNamespaced、VS/DR GetManagedLiveObjs成功。
- 固定Argo補正/merge+K8s applyは2kind×10/50/100/abort0でweight/hash保持PASS。
  旧desiredのhashなしでは削除を再現。配列内のapp label/port driftが正規化後も不一致として残る負例PASS。
- Python unit12件PASS。先頭prefix `/`、`/version`、先頭exact全method、未知regex遮蔽の4例を拒否。
  明らかに別path/prefix/POSTの先行routeの後ろにある正当routeは受入。
- platform unit5件PASS。空Application List gate、demo read3kindとwrite拒否を含む。
- 新3profile公式schema/4無効入力/analyze/通常native注入PASS。両固定render guard PASS。platform6資源K8s strict schema valid。
- 新fixture初回はcache manifestを保存する設定漏れ、JSON数値型の比較差で失敗。fixtureだけを修正して上記PASS。
  製品コードや固定版sourceを変更してテストを通したものではない。
- 全機能回帰・実clusterテストは追加していない。最新headのCIと再ReviewはManager確認対象。

**権限/登録の具体的差分**: 12:00承認済みのdemo controller/SA/Role/Core差分と非秘密namespace登録変更の中で、
「既存Account+demo追加」から「既存Application0件を条件に一時demo限定」へ手順を変更した。
追加readはdemo内のDeployment/AnalysisTemplate/AnalysisRunだけ。Account権限やSecret credentialへの拡張はない。
Managerはこの限定案を確認してからlocalへ渡す。Applicationが1件でも存在したらこの案のruntimeは停止する。
開始はdemo Role→demo-only登録→VS/DR inclusionの順。終了はApp/Project削除→VS/DR inclusionを元へ→登録namespace旧値→demo Role解除。
範囲を戻す前にinclusionを戻さないとAccount権限不整合が再発する。

Cloudでの実適用・Secret API操作・mergeは未実施。runtime ACは引き続き未検証。
固定版配列driftの自動修復、稼働ApplicationとのCore共用は保証範囲に含めない。


### 再Reviewの残P2修正と承認範囲訂正（2026-10-19追記）

先行routeの `path=/VERSION, caseSensitive=false` と `prefix=/VER, caseSensitive=false` を追加負例にすると、
修正前は2例ともValueErrorが出ず失敗することを再現した。未対応matcher/caseSensitive検査をpath/prefixの不一致returnより前へ移動。
matcher汎用化や新機能は追加していない。

`python3 -m unittest discover -s learning/istio/tests -p test_rollout.py -k Observation -v`:
修正前4 test中2 subcase失敗、修正後4 test PASS。新しい2遮蔽負例と既存の正常/不一致先行routeを含む。

前追記の「12:00承認済みの範囲」という解釈は誤りだった。
**demo-onlyへの登録置換・Account namespace一時除外、Core demoのDeployment/AnalysisTemplate/AnalysisRun read追加は追加承認待ち**。
ROLLOUTS.mdの現行説明を訂正した。実適用は禁止のまま、Cloudから適用/Secret操作/mergeは行っていない。
