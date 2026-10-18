# Issue 15 Implementation Handoff

## 目的

Account 1サービスのerror/latencyに基づく進行・abortを実装し、良好revisionへの復帰を独立Testへ渡す。
System `42521c0bd2a1220ef01ed009455a9372708f8314` のimplementation / single-issue / Markdown Handoffに従う。
3repo共通branchは `feat/account-metrics-analysis`。以下は固定コードcommit。
本Handoff追記だけのmanifest最終headはPR36で確認できる。

| repo | 固定main | 実装commit | 日本語Draft PR |
| --- | --- | --- | --- |
| application-manifest | `685eb6af0cb4e07f5f2b363d619034e116b056dc` | `4de7a0113d5e9d555de4de79a5bab9b644589045` | [#36](https://github.com/syuhei0519/application-manifest/pull/36) |
| platform-gitops | `e1be83c186fa60d2423c10bf341bc217992528d7` | `c086dd15cff0b822479bc688299223abde12755a` | [#93](https://github.com/syuhei0519/platform-gitops/pull/93) |
| backend-app | `882e0fab8d4e3c3261d4feeea325108bace43512` | `e2fe6153a70de35fdd51f989e845e5def8ab4c04` | [#55](https://github.com/syuhei0519/backend-app/pull/55) |

必要ACは正常進行、error/latency違反の非promotion/abort、Service/revision別queryの理由説明、
欠測/低sample/provider/query失敗/timeoutのfail-closed、良好revisionへ戻した代表API確認。
ソースとCloud自己検証までを担当し、cluster操作・main push・merge・Secret/Ruleset変更・追加課金APIは行っていない。

## やったこと

- manifest `learning/canary/`：明示metrics-good/candidate profile、10%準備pause→Prometheus inline Analysis→50/100。
  latest/stable各3 metric、5分/20要求/可用性99%/250ms以内95%、provider timeout5秒・error許容0、
  空/複数/非有限値/範囲外を非成功にする条件を追加。既存手動profileと#11固定結果providerは保持。
  公式CRD schema/構造・負例validator、固定版の実Prometheus provider試験、[local runbook](METRICS.md)を追加。
- platform `learning/monitoring/`：既存contrib0.161.0でnamespace補正→Pod UIDのmetadata関連付け→Pod label抽出。
  HTTP counter/histogramとgoroutines heartbeatだけにrollout_revision/learning_serviceを付ける。
  Collector SA/Podだけtoken mount、対象app namespaceのpods get/list/watch Role/Bindingだけを追加した。
  Node/Secret/ReplicaSet/他namespace権限と全resourceラベル化は追加していない。
  latest1/stable9のinstance被覆、各raw系列2点、up/SDK/HTTP鮮度、histogram整合を確認するqueryと26 fixture。
  generator出力とmanifest内のquery JSONは同一。既存dashboard/SLIを保持した。
- backend `performance/account/`：小helperと5 tests、既存isolated fixtureのlatency選択を追加。
  loopback限定・並列1・1 req/s・timeout2秒・retry/redirectなし、本体27 GET＋3 POSTの上限30要求。
  headers後のbody送信400ms遅延は合成入力であり、コード性能退行ではない。
  後片付けは別操作の最大3 GET＋3 DELETE。返った専用合成id/name/emailを照合してから削除する。
  合計最大36要求を本体30と区別し、cleanup前にAnalysisを評価する。
  CI変更は通常validate内のhelper test呼出し2行だけ。API本体/image/DB schema/PVC/既存Secret/migrationは変更していない。

Istioは対象外。ArgoへのConfigMap/Deployment createは再追加していない。
監視render/fixtureは承認済みlocal Test担当が別applyする。

## 観察結果

Cloud自己検証であり、独立Test/Reviewや実controller受入ではない。

| 再現コマンド | 結論 |
| --- | --- |
| manifest: `python3 learning/canary/validate.py --output .rendered/account-metrics` | 既存/metrics profile、固定公式Rollout/AnalysisTemplate/Run schema、復帰API一致、安全拒否が成功 |
| manifest: `go -C learning/canary/analysis-test test -timeout 90s ./...` | 既存web providerと実Prometheus provider成功。6 metricの正常/閾値違反/空/複数/NaN/Inf/範囲外/非JSON2xx/query・provider失敗、接続失敗、5秒timeoutを検査 |
| platform: `python3 learning/monitoring/validate.py --output .rendered/account-metrics` | Helm schema/strict lint、14資源、Pod-only RBAC/token/metadata境界、既存dashboard/SLI一致が成功 |
| platform: `sh learning/monitoring/check-tools.sh .rendered/account-metrics` | 固定Prometheusで既存RED/SLIとrevision 26ケース、固定Collector config validateが成功 |
| backend: `python3 -I performance/account/test_latency.py -v` | 5 tests成功。guard無通信、30要求、他行削除拒否、server入力遅延、2秒timeoutで再送なし |
| backend: 下記実APIコマンド | GET200×27、POST201×3、server最小402ms、専用行cleanup、既存sentinel保持、所有fixture削除が成功 |
| 3repo各root: `sh ci/test-docs-check.sh` | 各14回帰ケース成功 |
| 両render: 既存scan-gate `-check-render-only … -out /tmp/<summary>.json` | render構造境界成功 |
| 固定kubeconform v0.7.0 / Kubernetes1.36.0 | platform14/14成功。manifest built-in35成功、CR17はskipして上記公式schema検査で確認 |
| `cmp` platform query JSON / manifest chart内JSON、3repo `git diff --check` | 成功 |

実API再現（既存固定Docker fixtureを使用。binaryに実行可能bitが必要）:

```sh
source /workspace/.cloud-setup/activate.sh
cd /workspace/backend-app
mkdir -p .build/performance
CGO_ENABLED=0 go build -trimpath \
  -ldflags '-X main.buildCommit=882e0fab8d4e3c3261d4feeea325108bace43512' \
  -o .build/performance/metrics-server ./cmd/server
chmod 755 .build/performance/metrics-server
python3 -I performance/account/isolated.py \
  --binary .build/performance/metrics-server --profile latency \
  --output .build/metrics-latency-final
```

API sourceは基準backendと不変。helperは上表実装commitと同じ最終ソースを使用した。
最初の自己検証はbinaryがmode0700で非root container起動に失敗し、755への修正後に成功した。
provider timeout試験の最初のfixtureは終了待ちが残ってtest全体timeoutとなり、有限終了するfixtureへ修正後に成功した。
Go依存取得はworkspace内GOPATHを使用してchecksum検証を維持した。権限・検査を迂回していない。
途中の成功実APIは最小401ms、最終helperでの再確認は402ms。旧結果を最終値へ書き換えていない。
生成render/JSON/内部ログ/回復用cleanup.jsonはGitに追加していない。
CIはPR作成後の結果を別途確認する必要があり、Cloud自己検証からrequired成功とはしない。

未確認：実Podの2hash同時関連付け/export、controller生成Runのowner/hash一致、metrics正常による50%進行、
error/latency/欠測/少数/provider失敗時の実abort、良好desiredの再同期/代表API/元Deployment復帰。
#11の既受入を今回metrics ACの成功へ読み替えていない。

## 学び

同一imageのservice.versionは設定revisionを識別しない。既存SDKを変更せず、実Pod metadataで識別する。
Serviceへのport-forwardは1 Podを選ぶため、stable9のHTTP baselineは明示Podごとに作る必要がある。
upだけでは活性SDKを示さず、heartbeatだけでも一部HTTP欠測を示さないため両方を確認する。
低sampleと短い5分窓は学習用であり、本番SLAや窓全体の収集完全性を保証しない。

次はManagerが固定headへ独立Test/Reviewを割り当て、現在のCIとlocal結果を確認する。
localは [METRICS.md](METRICS.md) の順に、監視Role/Binding/tokenの承認済み差分を別apply→
metrics-good初回stable→metrics-candidate10%準備pause→全10 Podのbaseline/複数scrape→
stable/canaryの少数負荷→query/Run判定→違反abort→良好profileへdesired復帰/再sync2回→
代表API/基準行GET→元Deployment復帰/専用fixture・合成行cleanup。
権限の追加拡張、API故障注入framework、全feature再試験は不要。

### 独立Review P1: Application生成経路の訂正

初回Handoff後の独立Reviewで、platform generatorが `analysis.mode: metrics` を拒否すること、
valueFilesだけの変更では古いvaluesObject/AppProjectが残り、Analysis消失または許可不足になることを確認した。
初回のchart単体成功はApplication結合成功を示していなかった。

修正platform headは `58c6ae9ae07c4dd820e3d3d385193ea50ff6582c`（同じDraft PR93）。
明示metrics入力からmetrics-good/candidate profile、valuesObjectのenabled/mode/case、
AppProjectのRollout/Service/AnalysisTemplate許可を一括生成する。
mode省略のfixed互換とanalysis省略時のmanualを維持し、disabledとmetricsの組合せは拒否する。
AppProject以外のRole/権限は変更せず、ArgoのConfigMap/Deployment createを追加していない。
manifest PR36はMETRICS.mdと本追記だけを変更し、完全valuesからAppProject/Applicationを両方生成・適用する手順へ訂正した。
backend PR55のheadは上表から不変。

差分中心のCloud自己検証:

```sh
# platform root。既存の固定Python依存/PATH上のHelmを使う。
python3 learning/progressive-delivery/test_application.py --manifest-repo ../application-manifest
python3 learning/progressive-delivery/validate.py --output /tmp/issue15-p1-progressive
sh ci/test-docs-check.sh
# manifest root
sh ci/test-docs-check.sh
```

新結合回帰は10通りの生成Applicationを、そのvalueFiles→valuesObjectの実優先順でHelmへ渡し、
描画資源kindとAppProject許可集合の完全一致を確認して成功した。
metricsは10%→準備pause→Prometheus Analysis（6 metric）→50/100、manual/fixedも従来経路を維持。
3入力負例、Reviewer提示CLIによるmetrics入力の受理、既存AppProject/Application schema・限定権限・Lua health、
両repoのdocs回帰各14ケースとdiff checkも成功した。
CIへは固定manifest `a817e00f42a659d1dcd8a9766ad2727cc4beee18` のcheckoutとこの結合テストだけを追加した。
query26/実provider/helper/実API/metadata試験の全再実行はしていない。
P2のreset一律拒否はManagerから撤回の連絡を受領し、reset条件を変更していない。

この追記を含むmanifest最終headはPR36のheadで確認する。新headのCIと独立再Reviewは別途確認が必要。
local Testは修正済みplatform generatorを使い、保存するAppProject/Applicationと固定manifest commitを一組で引き継ぐ。
