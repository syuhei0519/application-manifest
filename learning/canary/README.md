# Account 手動Canaryと復帰

既存 `learning/chart` は変更していません。このAPI専用chartの明示利用時だけRolloutへ移行します。
描画対象はAccount Rolloutと3 Servicesだけ。DB/DB Service/PVC/Secret/ConfigMap/migration Jobを含みません。
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

```bash
# ローカル専用。CONTEXT/NAMESPACE/CONTROL_NAMESPACEは直前に検証した明示入力。
k() { kubectl --context="$CONTEXT" --namespace="$NAMESPACE" "$@"; }
c() { kubectl --context="$CONTEXT" --namespace="$CONTROL_NAMESPACE" "$@"; }
k scale deployment/learning-account --replicas=0
k wait --for=delete pod -l learning.local/workload=learning-account --timeout=120s
# 元Deploymentは0のまま保持。ここから初回Rollout ReadyまでAPI停止時間がある。
# 承認済みApplication（good）を登録し、pruneなしで初回手動sync。
c apply -f "$GITOPS_APPLICATION"
c patch application learning-account --type=merge \
  -p "{\"operation\":{\"sync\":{\"revision\":\"$MANIFEST_COMMIT\",\"prune\":false}}}"
k argo rollouts status learning-account-canary --timeout=300s
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
  -p "{\"operation\":{\"sync\":{\"revision\":\"$MANIFEST_COMMIT\",\"prune\":false}}}"
k argo rollouts get rollout learning-account-canary
# pauseConditions/currentStepIndex/RS desired・ready数・Pod annotation/imageIDを確認してから一段ずつ。
k argo rollouts promote learning-account-canary
# 50% pauseで同じ確認、API/基準行GET
k argo rollouts promote learning-account-canary
# 100% pauseで同じ確認。stable Podが0になり得るのでreplica比として記録。
k argo rollouts promote learning-account-canary
k argo rollouts status learning-account-canary --timeout=300s
c get application learning-account -o jsonpath='{.status.health.status}{"\n"}'
```

pauseではApplication health Suspended、進行中Progressing、完了Healthyを確認します。
GETが正常なだけで保存を証明せず、RolloutのPodを1つだけ削除して置換Ready後に同じ行をGETします。
DB/PVCを削除しません。削除対象はこのRolloutのownerReferenceを持つPod名を明示します。

### 3. failedをabort → 既知良好版をdesiredへ戻す → 再同期

この時点でHealthy確認済みcandidateを既知良好版とします。ApplicationのvalueFilesを`["../profiles/failed.yaml"]`へ更新し同じ手動syncを1回行います。
failed Podのreadiness失敗、Rollout status/conditions、API/基準行GETを観測し、timeoutを待たず停止入口を試します。

```bash
k argo rollouts abort learning-account-canary
k argo rollouts get rollout learning-account-canary
# stable RSへの復帰とAPI/基準行を確認。abortだけではdesiredのfailedは直らない。
c patch application learning-account --type=merge \
  -p '{"spec":{"source":{"helm":{"valueFiles":["../profiles/candidate.yaml"]}}}}'
c patch application learning-account --type=merge \
  -p "{\"operation\":{\"sync\":{\"revision\":\"$MANIFEST_COMMIT\",\"prune\":false}}}"
k argo rollouts status learning-account-canary --timeout=300s
# 同じdesiredで再syncをもう1回行い、failedが再投入されないことを確認する。
c patch application learning-account --type=merge \
  -p "{\"operation\":{\"sync\":{\"revision\":\"$MANIFEST_COMMIT\",\"prune\":false}}}"
```

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
c annotate application learning-account argocd.argoproj.io/skip-reconcile=true --overwrite
c delete application learning-account --cascade=orphan
# 一時的に両Workloadが存在する意図した復帰区間。先に元APIをReadyにする。
k apply -f "$OUT/deployment-workload.yaml"
k rollout status deployment/learning-account --timeout=300s
k apply -f "$OUT/deployment-service.yaml"
# Serviceが元selectorを持ち、API/基準行GETが成功してからRolloutを解除。
k delete rollout learning-account-canary --cascade=foreground --wait=true
k delete service learning-account-stable learning-account-canary
```

公開Serviceに残る `argocd.argoproj.io/tracking-id` annotationは、このApplicationの値だけを確認して解除します。
元DeploymentのPodを1つ置換し、基準行を再GETして試験行だけDELETEします。4API/4DB/監視Ready、同一DB/PVCを確認します。
失敗ならそこで停止してManagerへ返し、DBを作り直しません。CRD/controller全体の撤去はplatform側の別承認範囲です。
