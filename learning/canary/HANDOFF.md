# Handoff

## 目的

Lane A: platform-gitops #7、application-manifest #26/#25/#12のAccount手動Canaryと復帰。
System正本 `syuhei0519/ai-development-system@42521c0bd2a1220ef01ed009455a9372708f8314` の
implementation/single-issue/handoffを確認し、この単一Markdownへ集約した。

| repo | 固定main / 実装commit | branch / Draft PR |
| --- | --- | --- |
| platform-gitops | main `3a62ce5b087e7e2215b74028acac6b878679e40c` / code `239b4eb1c293348bb888ae2e36213cb657e10a92` | `feat/learning-account-canary` / [PR90](https://github.com/syuhei0519/platform-gitops/pull/90) |
| application-manifest | main `8e2490db23285869625c926c2c14f04ae8d3911a` / code `d859f55d25a276e57c235c8a366806ab311432ef` | `feat/learning-account-canary` / [PR34](https://github.com/syuhei0519/application-manifest/pull/34) |

code commit以後の本Handoff追記を含む最終HEADはPRで追える。どちらも未マージ。
backend/監視/Istioは変更していない。実cluster、新規kind、API課金、Secret値取得、main push、権限変更は実行していない。

## やったこと

- platform `learning/progressive-delivery/` とCI hookのみ追加。固定Argo CD Core v3.5.2 / Rollouts v1.10.1、
  upstream checksum・image digest固定、専用control namespaceとアプリnamespace内のRoles/Bindings、
  health Lua、独立サンプル、手動Account Application/AppProject生成、予算・承認一覧・解除範囲を用意。
  CRD/RBAC/controllerはGit管理された固定入力からCloud描画して手動bootstrapする案であり、
  基盤自身を継続同期する親Applicationは導入しない。
- manifest `learning/canary/` とCI hookのみ追加。既存chart/Deployment既定値は不変。
  Rolloutと公開/stable/canary Servicesだけを描画し、10 replicasで10/50/100＋無期限pause。
  selectorは旧Deploymentと分離。元Deploymentの0化→初回Rolloutへの移行は停止時間がある。
- 復帰modeはAPI Deploymentと公開Serviceだけ。同じsource/image/owner/SDK入力の既存APIと一致。
  DB/PVC/Secret/migrationを生成・再applyしない。APIをReady→Service切替→Rollout解除の復帰を記載。
- sourceはローカル事前確認で示された既存Account `e030275abbf52ecd27b7152ce597b3d89be3f830` を再利用。
  同一imageのgood(info)/candidate(warn)/failed(readiness404)という**設定revision**であり、バイナリ更新試験ではない。
  実image名は`local.learning/account:<source>-<確認済みconfig ID>`で固定する。
  事前確認Pod imageID `sha256:8e65f2220c26c1b54270d444b8eb4a5f9e269dda45f082013f31e49e9d487f87` と
  config/manifestの対応・image tagは未確認なので同一視していない。
- Gitに固定された3 profileをApplicationが明示選択。失敗後は選択を既知良好profileへ戻し、
  同じdesiredの再同期2回と保存したApplication定義の再applyで復帰が打ち消されないことを試す手順。
  自動sync/prune/selfHeal/削除finalizerや既存AppProjectの緩和は追加していない。

## 観察結果

Cloud自己検証（独立Test/Reviewではない）:

| 再現コマンド | 結果 |
| --- | --- |
| manifest: `python3 learning/validate.py` | 4サービスrender、schema/lint、Secret/DB/ownership/probes、Account telemetry/Order env境界と既存負例が成功 |
| manifest: `python3 learning/check_build_failfast.py` | 既存4 fixture成功。実image buildではない |
| manifest: `python3 learning/canary/validate.py --output /tmp/account-fixture` | 3revisionの固定公式Rollout schema、10/50/100、4 API資源、復帰API一致、17負例が成功 |
| platform: `python3 learning/progressive-delivery/validate.py --output /tmp/platform-fixture` | 公式checksum、6 CRDs選別、サンプルschema、Application/AppProject schema、限定Role、namespace負例、Lua health7ケース成功 |
| 各repo: `sh ci/test-docs-check.sh` / `sh ci/validate.sh` | 既存docs回帰14ケースと既存render検証が成功 |
| manifest: `(cd tools/inventory && go test ./...)` | 成功 |
| platform: `(cd tools/bootstrap && go test ./...)` | 成功 |
| 両repo結合 | 生成ApplicationのvaluesObject＋Git valueFilesを実Helmへ渡し、3profileが対応するRollout revisionを描画することを確認 |
| kubeconform v0.7.0固定CI image、`-strict -summary -kubernetes-version 1.36.0` | Core/Controller builtins＋復帰Deployment/Serviceの23資源が成功。container内TLSはhostの公開CA bundleをread-only mountして検証、TLS検証を無効化していない |
| 固定共有scan-gate、`-check-render-only /tmp/account-fixture` | 成功 |
| 同gate、`-check-render-only /tmp/platform-fixture` | **拒否**: 公式CRD内のschema宣言にLOCAL-HOST-NAMESPACE 12件。下記参照 |

依存はHelm4.2.4、PyYAML6.0.3/jsonschema4.26.0、platform Lua実行用lupa2.6。
CIへ同じstatic検証hookを追加し、新renderも既存 `.rendered` security経路に含めた。
PR作成時点のGitHub CIは両repoともvalidate実行中で、required成功は未確認。
CRDオブジェクト自体はkubeconform既定catalogでschema未提供のため、その検査を成功扱いにしない。
CRDは固定公式bytes＋metadata labelだけの変更、Rollout/サンプル/Applicationのinstance schemaは別途成功している。

**共有guardのブロッカー（未解消）**:
対象はbackend-app共有Action `ba8b3d211ac3e497900abd6c6f44f45388e3801c` の `tools/scan-gate/render.go`。
公式CRDの`hostNetwork/hostPID/hostIPC`のschema宣言を、汎用mapping walkが実Podの値と判断して拒否する。
実Podはhost namespaceを使っていない。guard除外・CRD隠し・schemaの削除・guard弱体化は行っていない。
platformの既存security stepもこのまま拒否する見込み。修正先はLane A所有外なので変更していない。
Managerが共有tool所有者へ調整し、schema宣言を区別しつつ未知PodSpecの拒否を維持する修正を別途Reviewする必要がある。
これは既存security gateによる拒否であり、sandboxのauto-review拒否ではない。

## 学び

Cloud実装と自己検証の範囲を完了したが、共有guardの解消、独立Review、required CI、実Kubernetes受入は未完了。
ローカル未準備を理由に実装を止めず、実環境操作だけを分離した。

次はManagerがguard所有者調整と独立Reviewを行い、ローカルTestへ両repoの固定commit/renderを渡す。
実行手順はmanifest `learning/canary/README.md`、導入権限一覧はplatform `learning/progressive-delivery/README.md`。
新control namespace、6 CRDs、SA3/Role/Bindings、常駐4 Pod、owner供給のCore用3 Secret前提は**実適用前に個別承認が必要**。
ClusterRole/cluster-admin/webhookは不要。RolloutsのSecret readは通知用固定名だけ、Account DB Secretを読めない。
Coreはcontrol namespace内のsettings/登録/Redis前提を参照する。password/tokenの生成・値取得は行わない。

未確認ACを完了扱いにしない:

- platform #7: controller Ready、独立サンプルRS生成/更新/解除、Argo実health、導入中の既存Deployment継続。
- manifest #26: schema/render・負例は成功。実image対応、移行時の旧Deployment0化と所有境界はローカル確認が必要。
- manifest #25: 実10/50/100 status/pause、replica数とtraffic比の区別、API CRUD・Pod置換後保存、image/source対応。
- manifest #12: 実promotion/abort、既知良好profileへ復帰、再同期2回と再apply、CRUD/保存維持、元Deployment復帰。

Account保存データ・他サービス・監視を保護し、同一imageのためmigrationの再実行は不要。
失敗/timeout時は対象APIの進行を止め、DB/Secret/PVCを再作成しない。独立ReviewはManagerの別担当であり、本自己検証で代替していない。
