# Accountのmetricsによる進行判定（Issue 15）

明示選択した `metrics-good.yaml` / `metrics-candidate.yaml` だけがPrometheusを使う。
既存の手動profileと#11の固定結果web providerは保持する。API/image/DB/PVC/Secret/migrationは変更しない。
Istio・traffic routingは次工程。10/50/100はreplica比であり、実traffic比の保証ではない。

## 判定と入力

10% → **準備用の無期限pause** → inline Analysis → 50% pause → 100% pause。
準備pauseでbaselineと少数負荷を確保し、通常の `promote` を1回行ってAnalysisを開始する。
`promote --full` やAnalysisRunの手動成功化は使用しない。初回stable作成はcontrollerがstepを省略するため、
初回goodをAnalysis合格とは数えない。

queryの正本はplatform `learning/monitoring/revision_queries.py` とその生成済み
`revision-queries.json`。同じJSONをこのchartの `files/revision-queries.json` に保持する。
変更時はgeneratorを更新し、platformで `python3 learning/monitoring/revision_queries.py --write`、
同ファイルをmanifestへコピーし、両validatorとpromtool/provider testを実行する。

selectorは `job=account-backend`、実 `account_namespace={{args.namespace}}`、
`learning_service={{args.service}}`、`rollout_revision={{args.latest-hash}}` / `{{args.stable-hash}}`。
controllerが生成したRunのownerReference、Latest/Stable argsを実Pod labelと照合する。
SDKの `service.version` は同じimageの設定revisionを分離しないため、判定には使わない。
`case` はPodTemplateを変える既存の実験markerとして残すが、metrics modeのqueryや閾値には影響しない。
負例を固定結果で作る仕組みではない。

| metric（latest/stable各3個） | 成功条件 | 理由 |
| --- | --- | --- |
| requests | 5分窓の推定増分20以上 | 既存学習SLIを継承。30要求の小負荷で確認可能にする |
| availability | 2xx+3xx / 全要求 が99%以上、1以下 | 4xxも分母へ含む。mixed-4xxで安全に違反させる |
| latency | 250ms以下bucket / count が95%以上、1以下 | 既存SDKの実bucketを利用。失敗応答も含む |

各系列で `increase` の後にinstance単位でsumし、最後にrevision全体をsumする。
各instanceのcounter resetを合計前に処理する。5分/20は学習用であり、低sampleでの統計的保証ではない。
stableも同じ閾値・窓・最低sampleで確認する。6 metricの値と解決済みqueryがRunに残り、同じ評価窓で比較できる。
判定は絶対閾値であり、stableより良ければ違反canaryも許す相対判定ではない。

## fail-closed

10%時点だけのgateなので、latestは**1 instance**、stableは**9 instance**が必要。
生きたinstanceはSDKのgoroutines heartbeatで識別する。heartbeatのtimestamp年齢は0以上90秒未満、
Collectorの `up` は明示namespaceの単一targetで1、0以上60秒未満。
9 stableのうち8しか観測できない場合も失敗する。Pod再起動直後に旧instanceが残れば一時的に非成功となる。

全instanceにHTTP counter/count/250ms/+Inf bucketの複数点、0以上90秒未満の鮮度を要求する。
countとcounter、+Infとcountの増分差は0.001未満、250ms bucketは0以上count以下。
一部instanceのHTTP欠測、無通信、20未満、histogram不整合はqueryが空になる。
成功status classだけがない場合は、**観測済みの分母×0**を分子に使い可用性0とする。
query全体へ無条件の `or vector(0)` は適用しない。

結果は有限値1個だけを許す。空、複数値、NaN、Inf、範囲外はFailed/Error。
Prometheus providerはtimeout5秒、failure/inconclusive/consecutiveErrorの許容0、count1。
query/provider失敗、非JSONの2xxもErrorで進行しない（固定版実providerのCloud test対象）。
controllerでのabort遷移は独立local Testで確認する。窓全体の完全な収集や個々のstatus class消失を証明するものではなく、
学習用の現在鮮度・instance被覆を確認する。背景HTTPも同じ5分窓に含まれる。

## local Testの順序

実操作は承認済みcontext/namespace/ownerに限定し、[既存復帰手順](README.md)のwrapperとoperation終了確認を使う。
ここに書かれた手順はCloudでのcluster実施結果ではない。

1. 3repoの固定headをcheckoutし、既存の非秘密values/source/imageを保持してrender/validatorを実行する。
   platform監視renderにはCollector token mountと、実app namespaceにpods get/list/watchだけのRole/Bindingが増える。
   Test担当が別途applyする。ArgoにConfigMap/Deployment createを追加しない。
   Collector SAが対象namespaceのPodを読め、他namespaceのPod・Secret・Node・ReplicaSetを読めないことを確認する。
   `k8sattributes` はnamespace補正後、Pod UIDだけで関連付けし、Pod labelのhash/serviceを抽出する。
   `resource_to_telemetry_conversion=false` を維持し、HTTP counter/histogramとheartbeatだけへ2 labelをコピーする。
2. 既存の完全な非秘密Account valuesを保持したまま、`revision: good` と
   `analysis: {enabled: true, mode: metrics, case: success}` を指定する（最上位の `mode: rollout` とは別）。
   修正済みplatform `learning/progressive-delivery/application.py` にこのvaluesと固定manifest headを渡し、
   **AppProjectとApplicationの両方**を新しい出力ファイルへ生成する。具体的なコマンドは下記。
   local Test担当が生成した両資源をapplyし、既存手順のoperation確認・手動syncで初回stableをReadyにする。
   generatorはmetrics-good profile、優先度の高いvaluesObjectのenabled=true/mode=metrics、
   AppProjectのRollout/Service/AnalysisTemplateだけの許可を同時に設定する。
   goodの初回はAnalysisを省略すること、telemetryが有効であることを確認する。
3. 同じ完全valuesの `revision` を `candidate` にして**両資源を再生成・apply**し、
   手動syncして10%準備pauseまで待つ。valueFilesだけをpatchしない。
   古いvaluesObjectのenabled=false/mode=fixedはprofileより優先され、古いAppProjectはAnalysisTemplateを拒否するため、
   profile・valuesObject・AppProjectを一組のdesiredとして更新する。
   live Ready Podの `rollouts-pod-template-hash`、Run args予定値、canary1/stable9を照合する。
   `target_info.k8s_pod_uid` と実Pod UID、HTTP/heartbeatのjob/instance/hash/serviceを確認する。
   同じimageの2hashが同時に分離/exportできない場合は停止して調査し、Analysisを始めない。
4. 全10 Podそれぞれへ明示Pod port-forward（loopback）で1回GETし、HTTP系列のbaselineを作る。
   Serviceへのport-forwardは1 Podを選ぶため、stable9の被覆確認には使えない。
   latency試験のcanaryは最初のHTTP export前に通常POSTを1回成功させ、その**専用合成行だけ**をDELETEし、
   POST/DELETEのstatus/route系列も用意する。既存行へwriteしない。seed分も別予算として記録する。
   SDK10秒＋batch5秒＋scrape30秒を考慮し、2回以上の異なるscrape点とcounter baselineを確認する。
5. stable用の確認済みPod転送先とcanary転送先にそれぞれbackend `performance/account/run.py` の
   `--profile normal --rate 1 --duration 30` を実行する。負荷終了後にも複数scrapeを確認する。
   backend READMEのguard入口を使い、毎回送った実要求数・UTC窓を記録する。
   6 queryを同じ時刻のPrometheus APIで確認してから `r promote learning-account-canary` でAnalysisへ進む。
   Runはcontroller生成であることを確認し、6 metric/値/hash/owner、成功後の50% pauseを記録する。
   50/100の手動promoteと代表GETは既存手順を使う。
6. error負例は、良好stableへ戻した別candidateでcanaryに `mixed-4xx` 30要求を送る。
   latency負例は別candidateへ下記helperを使う。`analysis.case=failure` / `inconclusive` 等を
   非秘密valuesで明示すればPodTemplate hashが変わる。metrics modeではmarkerだけで、実queryが判定する。
   同じhashを再使用する場合は古い負荷が5分窓から出るまで待ち、window汚染を区別する。
   各負例でstableの正常負荷・被覆を維持し、canaryの閾値違反、Run非成功、50%へ進まずabortすることを確認する。
7. 欠測/低sampleは負荷を行わない別hashで試す。稼働中instanceの一部HTTP欠測はCloud promtool fixtureで確認済み。
   provider失敗/timeoutは実固定provider対loopback fixtureで確認済み。実controllerまでの確認が必要なら、
   Test担当が別applyした専用Prometheus互換fixtureを使う。API/DB停止やArgo権限拡大を代用しない。
8. 違反後はoperation停止・終端確認の後、確認済み良好revision/caseの完全valuesからAppProject/Applicationを再生成し、
   両方をdesiredへ戻す。既存READMEの再sync2回・保存したApplication再applyを行う。
   `spec.paused=false`、failed RS縮退、良好hash Ready、代表API/保存した基準行GETを確認する。
   終了時は既存READMEのDeployment復帰を行い、専用fixture/転送/合成行だけ片付ける。
   元DB/PVC/既存Secret/migrationを再apply・削除しない。

Application/AppProjectの生成（platform checkoutでCloud実行。`ACCOUNT_VALUES` は既存の完全な非秘密values）:

```sh
python3 learning/progressive-delivery/application.py \
  --namespace "$NAMESPACE" --control-namespace "$CONTROL_NAMESPACE" \
  --manifest-commit "$MANIFEST_COMMIT" --values "$ACCOUNT_VALUES" \
  --output "$OUT/metrics-application.yaml"
# ローカル担当が両資源を確認・applyし、既存wrapperでpruneなし手動syncする。
```

再生成時は新規出力ファイルを使う。固定manifest commitとimage/source/owner/telemetry値を保持する。
case変更時も完全valuesから両資源を生成し直し、古いvaluesObjectを残したprofile patchで代用しない。

latency負例（確認済みのcanary loopback先だけ。並列1・1 req/s・timeout2秒・retry/redirectなし）:

```sh
python3 performance/account/latency.py run --target http://127.0.0.1:18080 \
  --revision "$CONFIRMED_ACCOUNT_SOURCE_SHA" --output .build/account-latency
# Analysisの評価を先に記録する。cleanupの正常HTTPを測定負荷と混同しない。
python3 performance/account/latency.py cleanup .build/account-latency/cleanup.json
```

本体は27 GET＋3 POSTの30要求。POSTはheaders後にbody送信を400ms遅らせる合成入力で、コード性能退行ではない。
後片付けは別操作の最大3 GET＋3 DELETE。要求全体の総数は最大36で、負荷30に混ぜず説明する。
local `cleanup.json` は戻った専用行id/identityだけのprivate回復用で公開しない。応答が不確実ならretry/一覧削除をせず
正確な合成identityを手動照合する。既存行との不一致は削除拒否。
Cloud実APIは201かつserver最小401msを確認したが、kindのrevision別exportとAnalysisによるlatency abortは未確認。
