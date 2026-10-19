# Handoff

## 目的

application-manifest #27 → #28 の Cloud 実装と自己検証。
branch `feat/istio-routing-27-28`、実装固定 commit `efe73251ebc9e92ed810f12d97169ff629560df9`。
この Handoff を追加した後の PR head は Git の履歴と PR で確認する。
開始は application-manifest `a4fe78845e2ab1e19d593b8511ca6f28fcd206b3`。
System 正本 `syuhei0519/ai-development-system@42521c0bd2a1220ef01ed009455a9372708f8314` の
implementation / single-issue / handoff に従う。
再利用元 platform-gitops `efd3a1f5a31eaf904bc787456034e4cebe932f7f` は読取のみ。
Account helper の Cloud 実行は backend-app `02b1018adaed8352a4651dd7fa8c73437e230296` の既存 API/DB を使用。

#27 は2 revision の render 整合・不正 subset/host/port と namespace/selector 逸脱拒否・同一 chart の既定復帰。
#28 は90:10/50:50の実到達を数える準備、条件と許容帯の固定、復帰後の既存 Account 代表 CRUD。
Cloud の担当は静的/契約テスト、実機手順と最小 helper。Kubernetes 実測は別 local Test の担当。

## やったこと

変更ファイルと理由:

- `learning/istio/chart/Chart.yaml`、`values.yaml`、`values.schema.json`:
  専用 namespace と固定 image、plain/stable/split90/split50 の有限入力。
- `learning/istio/chart/templates/demo.yaml`、`routing.yaml`:
  既存 client/v1/v2 と Service、VS/DR を再利用。default=100:0、90:10、50:50、plain を同じ chart に保持。
- `learning/istio/chart/files/demo.py`:
  revision 本文を数える逐次 HTTP helper。Connection close、no redirect/retry、最大20 req/s、timeout2秒。
  split90/split50 各1000要求、v2許容数60〜140/440〜560。失敗・未知本文を固定分母に含め全件不合格条件とする。
- `learning/istio/profiles/default.yaml`、`90-10.yaml`、`50-50.yaml`、`plain.yaml`:
  default 復帰と mesh OFF の違いを明示。plain apply だけでは VS/DR/v2 は削除されない。
- `learning/istio/validate.py`:
  固定 platform/Helm/Istio、公式 v1 CRD、render graph、20負例、既定復帰、通常/native sidecar 注入、offline analyze。
- `learning/istio/tests/test_measure.py`:
  合成応答による5テスト。許容境界、設定取り違え、不成功の分母、再試行なし、pacing、復帰、redirect を確認。
- `learning/istio/account_crud.py`:
  既存 Account API の1行を create/read/update/read/delete/404。自分の作成 ID だけを後片付けし本文を公開しない。
- `learning/istio/README.md`:
  local Test の準備、測定、XDS同期とnative配置、Account非影響確認、個別削除、必要権限と停止条件。
- `.github/workflows/ci.yml`:
  固定 platform checkout、checksum付き Istio binary、静的契約、Kubernetes schema と render artifact を既存 validate job に追加。
  required job名・security checks・read-only permissions・依存/image固定を維持。
- `docs/handoff-istio-routing-27-28.md`: この単一 Handoff。

platform-gitops / backend-app / frontend-app の tracked file 変更なし。実クラスタ・main push・merge・Secret/Ruleset/権限変更なし。
公開先は GitHub `syuhei0519/application-manifest` の上記 feature branch と日本語 Draft PR。
公開対象は上の16ファイルのみ。render・内部ログ・実行識別子・credential・Cloud helper は公開しない。

## 観察結果

2026-10-19 Cloud 自己検証:

| コマンド | 結果 |
| --- | --- |
| `python3 learning/istio/validate.py --platform "$PLATFORM" --istioctl "$ISTIOCTL" --output "$OUT"` | PASS: 4 lint/render、公式 v1 CRD、既定復帰、20負例、3 offline analyze、通常/nativeの全3 Pod注入 |
| `python3 -m unittest discover -s learning/istio/tests -v` | PASS: 5 tests。合成応答であり割合の実測ではない |
| `python3 learning/istio/account_crud.py --base-url http://127.0.0.1:8080` | PASS: Cloud の既存 PostgreSQL-backed Account 1行を CRUD、再取得、削除後404。開発サービスは終了済み |
| `sh ci/validate.sh` | PASS: 既存5 chart の lint/render |
| `bash ci/github/test.sh` | PASS: docs14、inventory/scan-gate Go、trusted render4、namespace sandbox3、Order schema11資源/改変10、release19 |
| pinned kubeconform `-strict -summary -kubernetes-version 1.36.0 -skip VirtualService,DestinationRule "$OUT"` | PASS: 29資源、Valid23/Invalid0/Errors0/Skipped6。VS/DR 6件は公式 schema で別検証 |
| `SECURITY_RENDER_REQUIRED=true bash ci/github/source-scan.sh` | PASS: 既存固定ポリシーで source/render 両方 true。warningは保持し、許容条件を変更していない |
| `git diff --check`、workflow YAML読込 | PASS: 既存 jobs / security step / read permissions 維持 |

再現には learning/requirements.txt、既存 lock と一致する Helm4.2.4/istioctl1.31.2、固定 platform checkout が必要。
CI の共有 scan-gate は既存 workflow と同じ backend-app `ba8b3d211ac3e497900abd6c6f44f45388e3801c` の
`ci/materialize-tools.sh` から `scan-gate` を配置して使用した。tracked source は変更しない。

Cloud の container はホスト側 CA bundle を自動参照しないため、kubeconform / Trivy は以下の条件で再実行した。
CI ソース・TLS検証・security policy の変更はない。`DOCKER_BIN` は `command -v docker` で取得する。
以下を一時 `docker` wrapper として PATH の先頭に置いた場合、上のCIコマンドをそのまま再現できる。

```sh
# ローカル環境専用 wrapper の動作。ホストの既存CAを読み取り専用で渡す。
if [ "$1" = run ]; then
  shift
  exec "$DOCKER_BIN" run \
    -v /etc/ssl/certs/ca-certificates.crt:/tmp/cloud-host-ca.pem:ro \
    -e SSL_CERT_FILE=/tmp/cloud-host-ca.pem "$@"
fi
exec "$DOCKER_BIN" "$@"
```

過去の失敗と修正を保持する:

1. 最初の offline 注入検証は公式 chart の空 YAML 文書の扱いで TypeError。空文書をスキップする修正1回後、全検証 PASS。
2. 既存 CI script の初回は共有 scan-gate の未配置で停止。CI 同様に固定版を materialize して解消。
3. CI / kubeconform / security scan は container の CA 不足で schema/DB取得が停止。既存ホスト CA の明示参照で解消。
4. Cloud の CA wrapper 初回は Docker binary path の誤指定で未実行。実際の `command -v docker` を使って解消。

環境再実行が2回を超えたことをここに報告する。実装コードの失敗修正は1回で、ACや担当範囲は拡大していない。
過去の失敗を成功へ書き換えず、以上の再実行結果を追記した。内部ログは公開しない。
security の PASS は warningなしを意味しない。既存依存等と今回の fixture の KSV-0125 警告は
既存ポリシーの warning として保持。AC外 hardening は行っていない。

未検証: Kubernetes の90:10/50:50実到達、runtime imageID/endpoint/XDS、実復帰後の Account CRUD、mesh OFFとcontrol撤去。
CI の実行結果は PR の現在の check を Manager が確認する。Cloud 成功だけで #28 の実機ACを完了にしない。

## 学び

weightの合計と公式schemaだけでは、別namespace/host/subset/selectorへの逸脱を検出しきれないため、renderの関係を負例で照合した。
90:10/50:50は非重複の許容帯と固定分母で区別する。接続を閉じてもEnvoyのpool/時系列相関は残るため、
少量負荷とactive configの記録を併用する。統計モデルや合成応答を実機到達と混同しない。

次の担当は local Test と独立 Review。README の baseline Account CRUD→default→90:10→50:50→default→Account CRUD→plain/撤去を
承認済み context/owner に限って実施する。必要権限は専用fixture CRUD/exec/status、namespace opt-in/解除の既存権限、
既存Account Service port-forwardと試験用1行のCRUD。control の権限は既存 platform 手順の範囲で Manager が判断する。
新しい platform 変更が不可欠なら実装前に Manager へ戻す。自己検証は独立 Test / Review を代替していない。
レビューと現在 CI 確認後の merge は Manager の担当で、この Draft は未統合。
