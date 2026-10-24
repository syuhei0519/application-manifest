# Account 手動Canaryと復帰

> Historical learning record. Its manual deployment route is retired for the existing kind.
> Use the [Git/PR → Argo deployment contract](https://github.com/syuhei0519/platform-gitops/blob/main/learning/existing-kind/DEPLOYMENT.md).
> The [original commands at fixed SHA](https://github.com/syuhei0519/application-manifest/blob/01ab7fc8c9c39f204957c6f06d7fc79c400e8b91/learning/canary/README.md)
> remain available as historical evidence, not as a current execution procedure.


既存 `learning/chart` は変更していません。このAPI専用chartの明示利用時だけRolloutへ移行します。
既定の描画対象はAccount Rolloutと3 Servicesだけ。DB/DB Service/PVC/Secret/migration Jobを含みません。
Issue #11の明示opt-in時だけAnalysisTemplateを追加します。HTTP providerは別のlocal Test専用fixtureです。
`mode=deployment,revision=good` は元のAccount Deploymentと公開Serviceだけを描画します。
復帰PodSpecは、同じsource/image/owner/telemetry値を渡した既存chartのAPIと一致します。

## 固定するrevisionと境界

- source: backend-app `e030275abbf52ecd27b7152ce597b3d89be3f830`。既存稼働Accountのcodeを再利用し、backendを変更しません。
- image: `local.learning/account:<上記source>-<実image config IDの64桁hex>`、pullPolicy Never。正式GHCR公開は前提ではありません。
- ローカル事前確認のPod imageIDは `sha256:8e65f2220c26c1b54270d444b8eb4a5f9e269dda45f082013f31e49e9d487f87`。
  これはruntime側の観測値です。config ID/manifest digestとの同一性やimage tagはCloud未確認です。
  Test担当は既存Deploymentのimage名・node image・config IDとの対応を再確認し、違えば停止します。
- good: annotation `learning.local/config-revision=good`, `LOG_LEVEL=info`, readiness `/readyz`。
- candidate: 同一image/source、annotation `candidate`, `LOG_LEVEL=warn`。API/schemaは共通です。
- failed: 同一image/source、annotation `failed`, `LOG_LEVEL=warn`, readiness `/__learning_not_ready`（意図した404）。
  imageを壊さず、失敗PodをService endpointから除外します。
- 10 replicas、maxSurge 0/maxUnavailable 1、10/50/100と無期限pause。安定後の目安は1/9、5/5、10/0です。
  移行中やabort直後の一時差は実statusで記録します。Meshなしのreplica比であり、実traffic比保証ではありません。
- 公開Service `learning-account` は両revisionのReady Podを対象にします。stable/canary Serviceは
  Rolloutsがhash selectorを更新します。既存Deploymentとはselectorを分け、RSを誤って共有しません。
- 同じimage/schemaなので新migration不要。既存完了Jobの再apply、DB/Secret/PVC再作成やdown migrationは禁止です。
  telemetryは既存どおり有効にし、SDK env/Collector宛先を維持します。

## Cloud検証

Helm 4.2.4、`../requirements.txt` のPython依存で:

```bash
python3 learning/validate.py
python3 learning/canary/validate.py --output /tmp/account-canary-fixture
```

出力は架空image IDによる試験用です。固定Rollouts v1.10.1の公式CRDに対するschema、3revision、
Service/Pod selector・port・namespace、所有外resource混入の拒否、復帰API一致を検証します。
既存chartの既定動作を変更していません。実Kubernetes、実imageの起動、API/保存データは未検証です。

## Review後のローカルTest（未実施）

platform `learning/progressive-delivery/README.md` の個別承認・導入・サンプル受入を先に行います。
現在のcontext/cluster/namespace/ownerを再照合し、他サービスと監視がReady、Account DB/PVCが同じUID、
migration Completeであることを確認します。秘密値を取得・出力せず、試験データのみを使います。
この手順は既存学習namespaceでのAPI移行用です。古い新規namespace初期化手順は実行しません。

Cloudで固定Manifest commitから次の非秘密valuesを使い、good/candidate/failed/deploymentの4ファイルを描画して渡します。
値は実image対応確認後に確定します。fixtureのimage IDを使ってはいけません。

```yaml
owner: <既存所有ラベル>
imageConfigID: sha256:<既存imageと対応確認したconfig ID>
sourceCommit: e030275abbf52ecd27b7152ce597b3d89be3f830
mode: rollout
revision: good
telemetry: {enabled: true}
```

```bash
# Cloud: 同じvaluesを用いて各revisionとDeployment復帰を描画する。
helm template learning-account learning/canary/chart --namespace "$NAMESPACE" \
  --kube-version 1.36.4 -f "$VALUES" --set revision=good > "$OUT/good.yaml"
helm template learning-account learning/canary/chart --namespace "$NAMESPACE" \
  --kube-version 1.36.4 -f "$VALUES" --set revision=candidate > "$OUT/candidate.yaml"
helm template learning-account learning/canary/chart --namespace "$NAMESPACE" \
  --kube-version 1.36.4 -f "$VALUES" --set revision=failed > "$OUT/failed.yaml"
helm template learning-account learning/canary/chart --namespace "$NAMESPACE" \
  --kube-version 1.36.4 -f "$VALUES" --set mode=deployment --set revision=good > "$OUT/deployment.yaml"
helm template learning-account learning/canary/chart --namespace "$NAMESPACE" \
  --kube-version 1.36.4 -f "$VALUES" --set mode=deployment --set revision=good \
  --show-only templates/api.yaml > "$OUT/deployment-workload.yaml"
helm template learning-account learning/canary/chart --namespace "$NAMESPACE" \
  --kube-version 1.36.4 -f "$VALUES" --set mode=deployment --set revision=good \
  --show-only templates/services.yaml > "$OUT/deployment-service.yaml"
```

### 1. APIと保存状態の基準 → 移行

Test担当は既存Account CRUD契約で専用の行を作成・GET・更新し、idと期待内容だけをprivate記録へ残します。
既存の学習データは変更しません。現在のDeployment API設定（image/source/SDK env/owner/Service selector）と
復帰renderが一致することを確認します。差があれば移行を止め、baselineを確定します。
同時に古いHelm/GitOps操作をしない担当合意を取ります。

`r` はplugin名の後へcontext/namespaceを渡す専用関数です。通常のkubectl操作は `k`/`c` を使います。
以下の各operation patch前には `.operation` が空であることを確認し、進行中operationを上書きしません。
通常syncでは次の表示を繰り返し、`operation=`（空）かつ `phase=Succeeded` を確認してから
次のprofile変更/operation patch/解除へ進みます。途中のRollout観測・一段ずつのpromoteは行えます。
Failed/Error/timeoutは成功扱いにせず停止します。意図的なfailed試験だけは、後述のabort後に終了確認します。
RolloutのhealthやpauseとApplicationの同期operation終了は別々に確認します。

```bash
# patch前、およびpatch後の完了確認。出力を確認するまで後続操作をしない。
c get application learning-account \
  -o jsonpath='operation={.operation}{"\n"}phase={.status.operationState.phase}{"\n"}'
```

```bash
# ローカル専用。CONTEXT/NAMESPACE/CONTROL_NAMESPACEは直前に検証した明示入力。
k() { kubectl --context="$CONTEXT" --namespace="$NAMESPACE" "$@"; }
r() { kubectl argo rollouts --context="$CONTEXT" --namespace="$NAMESPACE" "$@"; }
c() { kubectl --context="$CONTEXT" --namespace="$CONTROL_NAMESPACE" "$@"; }
k scale deployment/learning-account --replicas=0
k wait --for=delete pod -l learning.local/workload=learning-account --timeout=120s
# 元Deploymentは0のまま保持。ここから初回Rollout ReadyまでAPI停止時間がある。
# 承認済みApplication（good）を登録し、pruneなしで初回手動sync。
c patch application learning-account --type=merge \
  -p "{\"operation\":{\"sync\":{\"revision\":\"$MANIFEST_COMMIT\",\"prune\":false,\"syncOptions\":[\"RespectIgnoreDifferences=true\"]}}}"
# 上記のoperation終了確認（空 / Succeeded）後にRolloutを確認する。
r status learning-account-canary --timeout=300s
```

初回goodの作成は通常stepをスキップして全replicaがstableになります。これを10%観測と数えません。
旧Deploymentのreplicas=0と旧Pod不在、RolloutのReady、公開Serviceの疎通・基準行GETを確認します。
失敗なら後述のDeployment復帰へ進み、DBや完了Jobには触れません。

### 2. candidateの10 → 50 → 100 → 完了

Applicationの `spec.source.helm.valueFiles` をGit内のcandidate profileへ更新し、手動syncします。
Manifest targetRevisionは固定commitを維持します。3 profilesは同じGit commitで固定されており、選択profileがdesired revisionです。
Application自体は手動登録であり、別のGitOps親から再applyされる構成ではありません。

```bash
c patch application learning-account --type=merge \
  -p '{"spec":{"source":{"helm":{"valueFiles":["../profiles/candidate.yaml"]}}}}'
c patch application learning-account --type=merge \
  -p "{\"operation\":{\"sync\":{\"revision\":\"$MANIFEST_COMMIT\",\"prune\":false,\"syncOptions\":[\"RespectIgnoreDifferences=true\"]}}}"
# 各stepを観測する。Applicationのoperationが進行中なら、まだ次のsyncは行わない。
r get rollout learning-account-canary
# pauseConditions/currentStepIndex/RS desired・ready数・Pod annotation/imageIDを確認してから一段ずつ。
r promote learning-account-canary
# 50% pauseで同じ確認、API/基準行GET
r promote learning-account-canary
# 100% pauseで同じ確認。stable Podが0になり得るのでreplica比として記録。
r promote learning-account-canary
r status learning-account-canary --timeout=300s
c get application learning-account -o jsonpath='{.status.health.status}{"\n"}'
# operation終了（空 / Succeeded）を確認してからfailed試験へ進む。
```

pauseではApplication health Suspended、進行中Progressing、完了Healthyを確認します。
GETが正常なだけで保存を証明せず、RolloutのPodを1つだけ削除して置換Ready後に同じ行をGETします。
DB/PVCを削除しません。削除対象はこのRolloutのownerReferenceを持つPod名を明示します。

### 3. failedをabort → 既知良好版をdesiredへ戻す → 再同期

この時点でHealthy確認済みcandidateを既知良好版とします。ApplicationのvalueFilesを`["../profiles/failed.yaml"]`へ更新し同じ手動syncを1回行います。
failed Podのreadiness失敗、Rollout status/conditions、API/基準行GETを観測し、timeoutを待たず停止入口を試します。

```bash
# readiness失敗はstep10の自動pauseへ到達しない場合がある。明示pauseからabortする。
r pause learning-account-canary
k get rollout learning-account-canary -o jsonpath='{.spec.paused}{"\n"}'
r abort learning-account-canary
r get rollout learning-account-canary
# ここでは進行停止・abort状態・spec.paused=true・既存API/基準行GETを確認する。
# 明示pause中はcontrollerのRS調整も止まる。failed RS縮退や復帰完了をここで要求しない。
```

candidateへの変更前にfailed試験の同期operationを確認します。まだ `Running` なら、
Coreのoperation停止として次のpatchを行い、`.operation` が空、phaseが終端
（Succeeded/Failed/Error）になるまで確認します。Running以外ならこのpatchを実行しません。
Failed/Errorは失敗試験の結果として残します。終了しない/停止操作が失敗した場合は後続syncをせずManagerへ返します。

```bash
c get application learning-account \
  -o jsonpath='operation={.operation}{"\n"}phase={.status.operationState.phase}{"\n"}'
# operationが存在しphase=Runningの場合だけ。testで終了済みoperationの上書きを防ぐ。
c patch application learning-account --type=json \
  -p '[{"op":"test","path":"/status/operationState/phase","value":"Running"},{"op":"replace","path":"/status/operationState/phase","value":"Terminating"}]'
# operationが空・phase終端になったことを上のgetで確認してからcandidateへ戻す。
# abortだけではdesiredのfailedも明示pauseも直らない。
c patch application learning-account --type=merge \
  -p '{"spec":{"source":{"helm":{"valueFiles":["../profiles/candidate.yaml"]}}}}'
c patch application learning-account --type=merge \
  -p "{\"operation\":{\"sync\":{\"revision\":\"$MANIFEST_COMMIT\",\"prune\":false,\"syncOptions\":[\"RespectIgnoreDifferences=true\"]}}}"
# operation終了（空 / Succeeded）とchartのpaused:falseの同期を確認する。
k get rollout learning-account-canary -o jsonpath='{.spec.paused}{"\n"}'
# false確認後に初めてfailed RS縮退・既知良好candidateのReady/安定RS・API/基準行GETを確認する。
r get rollout learning-account-canary
r status learning-account-canary --timeout=300s
# 同じdesiredで再syncをもう1回行い、failedが再投入されないことを確認する。
c patch application learning-account --type=merge \
  -p "{\"operation\":{\"sync\":{\"revision\":\"$MANIFEST_COMMIT\",\"prune\":false,\"syncOptions\":[\"RespectIgnoreDifferences=true\"]}}}"
# operation終了（空 / Succeeded）、failed RSが0、candidate/API/基準行GETを再確認する。
```

CoreでのTerminatingへの遷移は固定版の
[TerminateOperation](https://github.com/argoproj/argo-cd/blob/e258ee23c3e52266d407572f4bcdfe7d9ed36cb5/server/application/application.go)
と同じ状態変更です。Applicationの同期停止とRolloutのabortを混同しません。

Gitの固定commitにあるgood/candidate/failed profileとApplicationの選択pathを併せてdesiredとして記録します。
Gitのchart/source/imageを変えず、失敗profileから確認済みcandidate profileへ選択を戻す操作です。
Cloudで`application.py`の非秘密入力revisionをcandidateへ更新してApplication定義を再生成し、
その保存した定義の再apply/再同期でもfailedが再投入されないことを確認します。
古いfailedのApplication定義を再applyしてはいけません。環境Application自体をGit管理する場合は、
そのGit入力もcandidate profileへ直すまで同期を止めます。今回は親Applicationを導入しません。
手動undoだけを永続的復帰扱いにせず、live Rollout・Application選択profile・固定Git profileの一致を確認します。
最低記録は変更理由、Manifest/source/imageとimageID対応、各pauseのstep/RS数、abort/復帰先、再sync2回、
CRUD・Pod置換後GET、Argo health、未達項目です。実行ID・内部ログ・認証値はGitへ載せません。

### 4. 元Deploymentへ戻す条件と操作

Controller/移行異常、保存状態異常、または学習終了で元Deploymentへ戻します。保存状態異常時は追加writeを止めます。
Applicationに進行中operationがないことを確認し、annotationでreconcileを停止してから
finalizerなしApplicationを削除します（cascading削除禁止）。これで古いRollout desiredの再投入を止めます。

```bash
# operationが空・phase終端であることを確認。進行中なら上記の停止/終了確認を先に行う。
c get application learning-account \
  -o jsonpath='operation={.operation}{"\n"}phase={.status.operationState.phase}{"\n"}'
c annotate application learning-account argocd.argoproj.io/skip-reconcile=true --overwrite
c delete application learning-account --cascade=orphan
# 一時的に両Workloadが存在する意図した復帰区間。先に元APIをReadyにする。
k rollout status deployment/learning-account --timeout=300s
# Serviceが元selectorを持ち、API/基準行GETが成功してからRolloutを解除。
k delete rollout learning-account-canary --cascade=foreground --wait=true
k delete service learning-account-stable learning-account-canary
```

公開Serviceに残る `argocd.argoproj.io/tracking-id` annotationは、このApplicationの値だけを確認して解除します。
元DeploymentのPodを1つ置換し、基準行を再GETして試験行だけDELETEします。4API/4DB/監視Ready、同一DB/PVCを確認します。
失敗ならそこで停止してManagerへ返し、DBを作り直しません。CRD/controller全体の撤去はplatform側の別承認範囲です。

## Issue #11: 固定結果によるinline analysis（opt-in）

`analysis: {enabled: true, case: success}` を明示したAccountだけ、10%の次をinline analysisに置き換えます。
成功すると**手動promoteなしで50% pauseへ**進み、その後の50/100% pauseは従来どおりです。
既定はdisabledで、従来の10/50/100%手動CanaryとDeployment復帰は同じです。
`revision=failed` はreadiness試験用なのでanalysisとの併用を拒否します。#15の実SLO、#8の全体適用は対象外です。

Rolloutから渡す引数はservice=`account`、実namespace、`Latest`/`Stable`のpodTemplateHash、caseです。
Pod annotation `learning.local/analysis-case` を変えるため、同一imageでもcase更新で新hash/新Runになります。
AnalysisTemplateの単一metricはcount=1、failureLimit/inconclusiveLimit/consecutiveErrorLimit=0、web timeout=1秒。
成功条件は値1と要求したservice/namespace/両hashの一致・非空を要求します。hash不一致はFailedです。

| case | HTTP fixture | provider measurement / Runの期待 | Rolloutの期待 |
| --- | --- | --- | --- |
| success | value=1、要求hashを返す | Successful | 50% pauseへ自動進行 |
| failure | value=0 | Failed | 自動abort、50%へ進まない |
| inconclusive | value=0.5 | Inconclusive | AnalysisRunInconclusiveでpause、50%へ進まない |
| missing | value自体を省略 | asFloatの評価Error | 自動abort、50%へ進まない |
| timeout | 3秒遅延（webは1秒timeout） | Error | 自動abort、50%へ進まない |

Argo管理chartの追加資源はAnalysisTemplateだけです。固定HTTP providerのConfigMap/Deployment/ClusterIP Serviceは
別chart `learning/canary/fixture` に分離し、local Test担当が事前apply・Ready確認・片付けを行います。
Application sourceには含めず、ArgoへDeployment/ConfigMapの作成・更新権限を追加しません。
fixtureは認証不要、Secret/ServiceAccount作成なし・Pod tokenなし。外部公開/Ingress/NodePortはありません。
固定Python imageはplatform `learning/istio` と同じdigestで、追加package不要です。
1 replica、requests 25m/32Mi、limits 200m/64Mi、read-only root filesystemと非root実行です。
このproviderは指定caseとhashを返す学習用であり、実revisionの品質や実SLOを測定しません。
AnalysisTemplateだけsync-wave=-1です。providerはArgoのsync waveやhealth判定では管理しません。
local Test担当が以下の独立fixtureを事前applyし、Ready確認後にApplicationを更新してください。

### Cloudで再現（clusterへ接続しない）

```bash
# Helm 4.2.4、Go 1.27.1、learning/requirements.txtのPython依存
python3 learning/validate.py
python3 learning/canary/validate.py --output /tmp/account-analysis-fixture
# 測定と条件式は固定Argo Rollouts v1.10.1の実web providerで実行する。
go -C learning/canary/analysis-test test -timeout 90s -v ./...
```

Python検証は固定公式CRDのchecksumとRollout/AnalysisTemplate/AnalysisRun schema、5つのPodTemplate差分、
権限外resource/改変step/hash引数等の拒否、既定Canary・復帰一致を照合します。
AnalysisRunのschema specimenは構造試験に過ぎず、controller生成の証拠ではありません。
Go検証はloopbackのPython fixtureへHTTPアクセスし、上の5caseとhash不一致を実providerで照合します。
Go moduleはcontrollerと同じv1.10.1に固定し、go.sumを保持します。
CIは同じ検証を実行し、架空imageのrenderを既存artifactに保存します。実配備には使いません。

### Manager承認・独立Review後のlocal Test（Cloudでは未実施）

1. platform側の追加Role/resource inclusionsと、opt-in AppProjectを先に固定commitから描画します。
   Managerが対象context/namespace/ownerと追加権限への適用を確認した後、local Test担当だけが適用します。
   既存DB/PVC/Secret/migrationと他サービス・監視を維持し、固定Python image到達性も確認します。
   下記の独立fixtureをlocal Test担当が事前applyし、Ready確認後だけAccountの同期へ進みます。
2. 本README既存の `k` / `c` / `r` を使います。pluginは必ず `kubectl argo rollouts --context=... --namespace=...` の順です。
   既存のoperation空・終端確認、prune=false、RespectIgnoreDifferences=trueをそのまま守ります。
   最初にanalysis enabled・case=success・revision=goodを既知良好stableにします。
   **初回作成がstep/analysisを飛ばしたことは成功受入に数えません。** 既存Rolloutの更新なら初期化中のRunも区別します。
3. Cloudで確認済みの実image valuesにanalysisを追加して、platform `application.py` で各caseのApplicationを生成します。
   `revision=candidate` と `analysis.case` を入力し、固定Manifest commitを維持します。
   `valuesObject.analysis` を含む更新Applicationを保存・再applyして、従来どおり手動syncします。
   進行中operationを上書きしません。新hash/新Runの生成を確認するまでcaseを変えません。
4. success更新を観測します。新RunのownerReferenceがAccount RolloutのUID、argsのlatest-hashが
   `.status.currentPodHash`、stable-hashが更新前の`.status.stableRS`と一致することを記録します。
   service/namespace/case、Run UID/name、measurement phase、Rollout step/phase/abort/pauseConditionsも残します。
   **promote前に**Run Successful→50% pauseを確認し、その後だけ50/100%を一段ずつpromoteしてstable化します。
5. 安定したsuccessを基準にfailure/inconclusive/missing/timeoutを一つずつ更新します。
   各caseのannotation、新hash・新Runとargs、終端Run phase、50%へ未進行を確認します。
   Failed/Errorは自動abort、Inconclusiveはpauseを要求します。**負例を手動promoteしてはいけません。**
   Run待ちやApplication operationが止まった場合は成功扱いせず記録して停止します。
6. 各負例の後は必要なら `r abort learning-account-canary` を実行し、既存手順で同期operationを終了確認します。
   desired Applicationのcase=success・既知良好revisionへ戻して再同期します。
   明示 `r pause` 後のabortは `.spec.paused=true` を残し得ます。abort単体を復帰完了とせず、
   chartのpaused:false同期・安定RS・API/基準行GETと同じdesiredでの再同期を確認してから次caseへ進みます。

観測コマンド例（既存関数と、実際にRolloutが生成したRun名を使用）:

```bash
r get rollout learning-account-canary
k get rollout learning-account-canary -o jsonpath='hash={.status.currentPodHash}{"\n"}stable={.status.stableRS}{"\n"}step={.status.currentStepIndex}{"\n"}abort={.status.abort}{"\n"}pause={.status.pauseConditions}{"\n"}'
k get analysisruns -o wide
k get analysisrun "$RUN" -o jsonpath='owner={.metadata.ownerReferences}{"\n"}args={.spec.args}{"\n"}phase={.status.phase}{"\n"}metrics={.status.metricResults}{"\n"}'
```

runtime受入は5caseのRun/Rollout statusが揃って初めて判断します。Cloud/CI成功だけではAC完了ではありません。
試験終了時は進行中operationを終了し、既存Deployment復帰手順を守ります。analysis disabledへ戻すだけでは
prune=falseのためAnalysisTemplateは消えません。Application停止・Account復帰後、所有UID/labelと他利用なしを確認して
`learning-account-analysis` AnalysisTemplateを個別解除し、独立fixtureはlocal Test担当が下記の手順で片付けます。
生成RunはownerReferenceと完了状態を確認して個別に扱い、namespace/DB/CRDを一括削除しません。


### 独立provider fixtureの事前配置と片付け

Cloudで以下を描画します。Applicationのchart/path/valueFilesは変更せず、この出力をArgo syncに含めません。
入力はownerと明示learning-* namespaceだけで、image/URL/名前の上書きは拒否します。

```bash
# Cloud: 非秘密の確認済みowner/namespaceを使用する。
helm template learning-account-analysis-provider learning/canary/fixture \
  --namespace "$NAMESPACE" --kube-version 1.36.4 --set-string owner="$OWNER" > "$OUT/provider-fixture.yaml"
# local Test: 同名資源の不在、context/namespace/ownerと描画3資源を照合してから実行。
k rollout status deployment/learning-account-analysis-provider --timeout=120s
# Ready/Service endpointを確認後にのみAccount Applicationを同期する。
# 試験終了・Account復帰後: 所有UID/labelと他利用なしを再確認する。
k delete deployment learning-account-analysis-provider --wait=true
k delete service learning-account-analysis-provider
k delete configmap learning-account-analysis-provider
```

fixture失敗・未ReadyならAccount更新を開始しません。既存の同名資源は無条件に上書きせず停止します。
fixtureへの追加apply/削除はlocal Test担当の操作であり、Argo SAへの一般Deployment/ConfigMap権限追加で代替しません。

## Metrics gate（Issue 15）

Prometheusによる明示opt-in profile・準備pause・欠測拒否・負荷/復帰は [METRICS.md](METRICS.md) を参照。
既存の手動/固定結果Analysisの手順と履歴は保持する。
