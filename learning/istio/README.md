# Istio の重み付き routing（#27 → #28）

専用 `learning-istio-demo` の client → mesh-demo の GET `/version` だけを対象にする。
control は既存の `istio-learning-system` / revision `learning-1-31-2`。
platform-gitops `efd3a1f5a31eaf904bc787456034e4cebe932f7f` の
`learning/istio` chart の client/v1/v2、固定 image、securityContext、Service と撤去方式を再利用した。
platform 側の変更はない。chart はこの repo の `learning/istio/chart` を唯一の routing desired とし、
同名の platform stable/split を並行 apply しない。Helm は render に使い、配備は保存済み raw YAML の手動 apply。
ユーザーの引継ぎでは #8 の client/v1 通信と撤去は完了、mesh OFF、v2 未配置。この作業では実状態を参照していない。
この手動routing手順では Argo Rollouts × Istio、全サービス mesh、全体 mTLS 強制、追加監視は扱わない。
#14 のController/GitOps統合は [専用Rolloutシナリオ](ROLLOUTS.md) を参照する。

## Cloud の静的検証

Helm 4.2.4、Istio 1.31.2、Kubernetes 1.36.4 の固定条件。
既存 `versions.lock.json` の Helm / istioctl checksum と image digest を照合し、
既存 vendor CRD の `networking.istio.io/v1` schema で VS/DR を検査する。
platform checkout は指定 SHA の変更なしの source を使う。

```sh
python3 -m pip install -r learning/requirements.txt
# PLATFORM は上記固定 SHA の platform-gitops checkout、ISTIOCTL は lock と一致する binary。
python3 learning/istio/validate.py --platform "$PLATFORM" --istioctl "$ISTIOCTL" --output "$OUT"
python3 -m unittest discover -s learning/istio/tests -v
```

`OUT` は未作成のディレクトリ。default / 90-10 / 50-50 / plain の4ファイルを生成する。
4 profile の lint、namespace・Service・port・selector・revision・subset・weight の整合、
不正値と改変 render 20件の拒否、profile 無指定と default 復帰の一致を確認する。
`istioctl analyze --use-kube=false` と明示ローカル設定での kube-inject を実行し、
通常 proxy container と native sidecar（initContainers の proxy + restartPolicy Always）の両配置を確認する。
注入済み出力は検査専用であり配備しない。自動注入との二重適用をしない。
`--istioctl` を省略した実行は注入/API analyze を未実施と表示する。CI は省略しない。
helper の合成応答テストは会計の検証であり、実トラフィック比率の証拠ではない。

## ローカル Test の準備と必要権限（Cloud 未実施）

独立 Review と Manager が認めた context・所有範囲で local Test が実行する。
platform の既存 README / acceptance.md の所有照合、固定 tool・image、導入と撤去手順を使用する。
CRD/control の導入が必要なら既存の認可範囲を再確認し、既存保護を変更しない。
新しい platform 設定や権限が不可欠なら停止して Manager へ戻す。

- fixture の既存 SA/CM/Service/client/v1 は前回の所有 UID と照合する。v2/VS/DR は不存在または今回の所有を確認する。
- control/namespace label は既存 `namespaces.yaml` と revision を使う。legacy injection / ambient を併用しない。
- 認可済み運用者に専用 namespace の Deployment/Service/ConfigMap/ServiceAccount/VS/DR の get/list/create/patch/update、
  Ready/EndpointSlice の参照、client/v1 の rollout restart、client Pod exec、所有資源 delete が必要。
  namespace label 更新と control の操作は platform 既存手順の認可のみ。Argo の権限を増やさない。
- Account は既存非参加 workload と DB/PVC/Secret を保持する。認可された Account Service の参照・port-forward と
  試験用 Account 1行の CRUD 権限が必要。Secret 内容取得・変更を要求しない。
- 上の4 render と platform の保存済み base/istiod/namespaces/plain を用意する。
  実 node の architecture と固定 imageID、3 fixture + 3 proxy の容量を確認する。
  fixture 合計 requests 75m/96Mi、limits 600m/192Mi。proxy は既存予算で合計 requests 300m/384Mi、limits 1500m/768Mi。
  control は既存 500m/512Mi（limit 1000m/1024Mi）。init の最大値・Pod overhead は別に照合する。

以下は将来の実機操作用。context を省略しない。`OUT` はこの repo の固定 commit から描画した出力。
開始前に Account の同じ CRUD helper を実行して baseline を記録し、既存 Account Pod の proxy 不在も確認する。
fixture の割合測定を Account CRUD の代替にしない。

```sh
: "${ISTIO_CONTEXT:?verified context}"
: "${OUT:?verified render directory}"
k() { kubectl --context "$ISTIO_CONTEXT" -n learning-istio-demo "$@"; }
# 既存 platform 手順で必要時だけ control を復元し、namespace revision opt-in を確認した後。
k apply -f "$OUT/default.yaml"
# 既存 client/v1 は opt-in 後の再作成が必要。新規 v2 は自動注入を確認する。
k rollout restart deployment/mesh-demo-client deployment/mesh-demo-v1
k rollout status deployment/mesh-demo-client --timeout=120s
k rollout status deployment/mesh-demo-v1 --timeout=120s
k rollout status deployment/mesh-demo-v2 --timeout=120s
```

3 Pod の Ready、fixture/proxy imageID、revision、init/proxy が通常または native であることを確認する。
Service selector は app=mesh-demo、両 endpoint は version=v1/v2 で Ready、port=8080。
client は Service の endpoint に入らない。client の source Envoy を通して測り、Pod IP 直打ちや
Service への port-forward は割合測定に使わない。
`istioctl --context "$ISTIO_CONTEXT" proxy-status` と対象 client の proxy-config routes/clusters/endpoints を用いて
ACK/SYNCED、対象 FQDN・subset・port と active route weight が desired と一致してから窓を開始する。
伝播待ち中の窓は受入に使わず理由を残す。

## 固定測定と復帰

| 窓 | 要求数 | v2 の許容数 | 条件 |
| --- | ---: | ---: | --- |
| warmup | 20 | 0〜20 | 本測定とは別。失敗・未知本文は0 |
| stable / plain | 各200 | 0 | 全件200/v1 |
| split90 | 1000 | 60〜140（6〜14%） | v1 は残り、失敗0 |
| split50 | 1000 | 440〜560（44〜56%） | v1 は残り、失敗0 |

各割合に独立した一つの窓を事前固定し、合算、成功要求だけで再正規化、通るまで繰返し、途中打切りをしない。
90:10 の±4 percentage points と 50:50 の±6 points は二項モデルの標準誤差に対し各約4.2/3.8倍。
許容帯は重ならず、相互の設定の取り違えや100:0を受け入れない。これは学習用の事前固定の判定で、
厳密な独立同分布や本番SLOの証明ではない。Envoy の HTTP request 単位の routing を前提に、
逐次 HTTP/1.1、新規 Connection: close、再試行なし・redirectなし、timeout=2秒、要求後50ms待機で最大20 req/s。
client の接続再利用を避けても Envoy upstream の接続 pool・endpoint数・時系列相関の影響は残るため、
active config、Pod/endpoint の安定、窓の開始/終了を併記する。失敗/未知本文も固定分母に1回だけ含め、1件でも不合格。
1000件窓は正常時最低50秒、各要求が timeout なら最大約2050秒 + 処理時間。並列負荷はかけない。

```sh
measure() { k exec deployment/mesh-demo-client -c fixture -- python3 -B /fixture/demo.py measure "$1"; }
measure warmup
measure stable
k apply -f "$OUT/90-10.yaml"
# active route が90:10、endpoint安定、XDS同期を確認する。
measure warmup
measure split90
k apply -f "$OUT/50-50.yaml"
# active route が50:50、endpoint安定、XDS同期を確認する。
measure warmup
measure split50
k apply -f "$OUT/default.yaml"
# active route が100:0、XDS同期を確認する。defaultでもv2は残し、到達0を検証する。
measure warmup
measure stable
```

結果には profile・manifest commit・tool版、要求数、v1/v2、失敗種別、時刻、割合、許容帯と判定を記す。
失敗窓は残し、原因が判明した場合の再試験は別窓として追記する。境界を事後変更しない。

復帰100:0の確認後、既存 Account の同じ認可済み Service に loopback port-forward する。
Account namespace/Service を推測せず local Test が実名を入力し、既存 Pod image/config/UID と proxy 不在を確認する。

```sh
: "${ACCOUNT_NAMESPACE:?verified Account namespace}"
: "${ACCOUNT_SERVICE:?verified Account Service name}"
# 別terminal。既存 Service の port=8080 を確認してから。
kubectl --context "$ISTIO_CONTEXT" -n "$ACCOUNT_NAMESPACE" port-forward --address 127.0.0.1 "service/$ACCOUNT_SERVICE" 18080:8080
# baseline と default復帰後の両方で同じコマンドを実行する。
python3 learning/istio/account_crud.py --base-url http://127.0.0.1:18080
```

helper は専用一意 email の1行を作成201→取得200→更新200→再取得200→削除204→取得404を確認する。
自身で作成した ID だけを finally で削除し、本文や ID を出力しない。異常終了時は私有記録で残存確認・後片付けを行う。
この代表 CRUD は routing 復帰後の非影響確認であり、Account 自体が重み分割されたとは主張しない。
追加の全API回帰は行わない。Cloud の loopback DB-backed 成功は Kubernetes の復帰後 AC を満たしたことにはならない。

## 撤去・mesh OFF への復帰

割合または CRUD が失敗したら次の割合へ進めず、まず同じ chart の default を再applyし同期と安定性を確認する。
原因不明・復帰未確認なら Manager へ返す。撤去時は default の200件/CRUD成功後、今回所有する資源だけを扱う。

```sh
# default維持中にv2を除去。100:0なのでv1へ継続して到達することを確認する。
k delete deployment mesh-demo-v2 --wait=true
k delete virtualservice mesh-demo
k delete destinationrule mesh-demo
# 既存 platform 手順と同じ。label省略だけでは解除できない。
kubectl --context "$ISTIO_CONTEXT" label namespace learning-istio-demo istio.io/rev-
k apply -f "$OUT/plain.yaml"
k rollout restart deployment/mesh-demo-client deployment/mesh-demo-v1
k rollout status deployment/mesh-demo-client --timeout=120s
k rollout status deployment/mesh-demo-v1 --timeout=120s
measure warmup
measure plain
```

plain apply だけでは v2/VS/DR は消えないため上の個別削除が必要。
2 Pod の proxy/init 不在、v1 のみの EndpointSlice、VS/DR/v2 不在を確認し Account CRUD を再実行する。
port-forward を終了する。control 撤去は既存 platform の所有照合と順序に従い、CRD/namespace/CA Secret を一括削除しない。
秘密値、内部ログ、非公開実行ID は Git/PR に公開せず、必要な集計と未検証点だけを Handoff に残す。
