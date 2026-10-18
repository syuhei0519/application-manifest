# Handoff

## 目的

Lane A: platform-gitops #7、application-manifest #26/#25/#12のAccount手動Canaryと復帰。
System正本 `syuhei0519/ai-development-system@42521c0bd2a1220ef01ed009455a9372708f8314` の
implementation/single-issue/handoffを確認し、この単一Markdownへ集約した。

| repo | 固定main / 実装commit | branch / Draft PR |
| --- | --- | --- |
| platform-gitops | main `3a62ce5b087e7e2215b74028acac6b878679e40c` / code `6959b6fb22216405952f02a3ae819cc4bcc6e357` | `feat/learning-account-canary` / [PR90](https://github.com/syuhei0519/platform-gitops/pull/90) |
| application-manifest | main `8e2490db23285869625c926c2c14f04ae8d3911a` / code `324f4249bec3b2d24fc77a525711c0f1648be7d4` | `feat/learning-account-canary` / [PR34](https://github.com/syuhei0519/application-manifest/pull/34) |

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
| platform: `python3 learning/progressive-delivery/validate.py --output /tmp/platform-fixture` | 公式checksum、6 CRDs選別、サンプルschema、Application/AppProject schema、限定Role/leader-election Lease権限、namespace負例、Lua health8ケース成功 |
| 各repo: `sh ci/test-docs-check.sh` / `sh ci/validate.sh` | 既存docs回帰14ケースと既存render検証が成功 |
| manifest: `(cd tools/inventory && go test ./...)` | 成功 |
| platform: `(cd tools/bootstrap && go test ./...)` | 成功 |
| 両repo結合 | 生成ApplicationのvaluesObject＋Git valueFilesを実Helmへ渡し、3profileが対応するRollout revisionを描画することを確認 |
| kubeconform v0.7.0固定CI image、`-strict -summary -kubernetes-version 1.36.0` | Core/Controller builtins＋復帰Deployment/Serviceの23資源が成功。container内TLSはhostの公開CA bundleをread-only mountして検証、TLS検証を無効化していない |
| 固定共有scan-gate、`-check-render-only /tmp/account-fixture` | 成功 |
| 同gate、`-check-render-only /tmp/platform-fixture` | **拒否**: 公式CRD内のschema宣言にLOCAL-HOST-NAMESPACE 12件。下記参照 |

依存はHelm4.2.4、PyYAML6.0.3/jsonschema4.26.0、platform Lua実行用lupa2.6。
CIへ同じstatic検証hookを追加し、新renderも既存 `.rendered` security経路に含めた。
GitHub CI: platform初回commitはSource and rendered security checksでvalidate/ci-resultが失敗。
追記した最新codeのCIとmanifest CIは実行中で、required成功は未確認。
CI security-results artifactのdownloadは配信先Azure BlobでForbiddenとなり取得拒否。
job/stepの失敗summaryは取得済みだが、remoteの詳細findingは未取得で、ローカル12件と同一原因と断定していない。
この取得を別経路へ迂回していない。
CRDオブジェクト自体はkubeconform既定catalogでschema未提供のため、その検査を成功扱いにしない。
CRDは固定公式bytes＋metadata labelだけの変更、Rollout/サンプル/Applicationのinstance schemaは別途成功している。

**共有guardのブロッカー（未解消）**:
対象はbackend-app共有Action `ba8b3d211ac3e497900abd6c6f44f45388e3801c` の `tools/scan-gate/render.go`。
公式CRDの`hostNetwork/hostPID/hostIPC`のschema宣言を、汎用mapping walkが実Podの値と判断して拒否する。
実Podはhost namespaceを使っていない。guard除外・CRD隠し・schemaの削除・guard弱体化は行っていない。
platformの既存security stepは初回CIで失敗しており、このローカル拒否も未解消。修正先はLane A所有外なので変更していない。
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

### Review指摘の限定修正（2026-10-18追記）

上記の初回失敗・未実施記録は保持する。今回のcode commitは
manifest `64909ec615ab36a6d0a4b91825c01019b5f16a55`、
platform `63cef5817a68797e17059db5f8f349aa36df09f0`。branch/既存PRは上表と同じ。
manifestの本追記を含む最終HEADはPR34で追える。

- manifest `learning/canary/README.md`: plugin専用 `r()` へ全11呼出しを置換。
  abort直後は進行停止/abort/既存API維持だけを確認し、candidateのGit profile同期と
  `spec.paused=false` 確認後にfailed RS縮退・既知良好版/API復帰を判定する順序へ修正した。
  各sync前後のoperation確認、意図したfailed試験の進行中operation停止と終端確認、
  直接operation patch全4箇所の `RespectIgnoreDifferences=true` を追記した。
- platform `.github/workflows/ci.yml`: 共有Actionを `fa921cd2aace14f9052b79aa6dc6f37cb07dd274` に固定。
  `learning/progressive-delivery/README.md` に同じ同期境界とCore3 Secretの未解消前提を記載。
  空settings/非秘密登録は個別承認後のlocal bootstrap、Redis authはユーザー管理のprivateファイルから
  最終作成する手順を示した。認証情報の生成/入力/取得やcluster操作は実施していない。
- 同imageのinfo/warn/readiness404設定revision、replica比の試験範囲を維持。
  chart/render/DB/PVC/Secrets/migrationは変更していない。バイナリ更新や実traffic10%の受入ではない。

自己検証:

```bash
# manifest（既存固定依存とHelm 4.2.4を使用）
python3 learning/canary/validate.py --output /tmp/review-account-render
# platform（既存固定依存を使用）
python3 learning/progressive-delivery/validate.py --output /tmp/review-platform-render
```

両方成功。manifestは公式Rollout schema/3revision/API専用4資源/復帰API一致/17負例、
platformは公式checksum/6 CRDs/限定RBAC/Application schema/8 Lua healthケースが成功した。
platformの描画script/入力は元commitから不変。同じfixture renderを新guardへ渡す検証は下記配送待ち。
pluginの通信しない回帰確認は、manifest repoで次を実行できる:

```bash
python3 - <<'PY'
import re, subprocess
from pathlib import Path
text = Path('learning/canary/README.md').read_text()
for block in re.findall(r'```bash\n(.*?)```', text, re.S):
    subprocess.run(['bash', '-n'], input=block, text=True, check=True)
defs = '\n'.join(re.findall(r'^[krc]\(\) \{.*$', text, re.M))
calls = re.findall(r'^r .*$', text, re.M)
stub = '''kubectl() { [[ "$1" == argo && "$2" == rollouts && "$3" == --context=fixture-context && "$4" == --namespace=learning-fixture ]]; }
CONTEXT=fixture-context
NAMESPACE=learning-fixture
'''
subprocess.run(['bash', '-e'], input=stub+defs+'\n'+'\n'.join(calls), text=True, check=True)
assert subprocess.run(['bash'], input=stub+defs+'\nk argo rollouts status learning-account-canary\n', text=True).returncode != 0
assert len(calls) == 11 and 'k argo rollouts' not in text
patches = re.findall(r'^c patch application learning-account --type=merge \\\n  -p (.*)$', text, re.M)
ops = [p for p in patches if 'operation' in p]
assert len(ops) == 4 and all('RespectIgnoreDifferences=true' in p for p in ops)
print('PASS bash syntax; 11 plugin-first calls; old wrapper rejected; 4 sync options')
PY
```

実行結果は成功。実kubectl/pluginやcontrollerによる復帰受入を代替しない。
`git diff --check` も両repoで成功。今回のCI結果は公開後に確認するため、この時点で成功とはしない。

**Lane B配送の阻害（未解消）**: 非公開Libraryの元commit archiveは、現在の公式materializeと
consumer-local宛先への1回の再試行の両方で `download failed`。helperはHTTP statusを返さず、
401/403と断定していない。ローカルファイルを読めないため、bundle復元・prerequisite/commit同一性確認・配送pushは未実施。
一般ダウンローダ/別資格情報/権限拡張へ迂回せず、元commitを再生成していない。archiveの公開保存もしていない。
正規Git readでbackend PR54は `e8bec488994debb100571676ebd94bec5c2bbc6f`、
platform PR89は `336a2e6c0d556935c6da2f4adb127d8fa1190dfe` と基準一致を確認。
共有guard SHAのGit fetchは `not our ref`。新Action参照はこの元commitの公開待ちで、
guard成功/required CIは未確認。Bの2 branchは変更していない。

次の担当はManager。元archiveの正規配送が可能になった環境で、head/祖先/prerequisiteを確認し
backend→platformの順に元commitだけをfast-forward配送する。その後新guardで同render/CIを確認し、
Lane A/Bの固定SHAへ独立Reviewを再依頼する。実cluster受入とCore3 Secret前提は引き続き未完了。
