# learning/account-release/metric-plugin の見方

Argo Rollouts から呼ばれ、計測対象の版と結果を検証する Go plugin。

## 構成と入口

[main.go](main.go) が Rollouts からの要求を受け、[runtime.go](runtime.go) が計測対象の実体を照合します。queries.json は生成された入力です。


主なファイルの入口です。

| ファイル | 役割 |
| --- | --- |
| [Dockerfile](Dockerfile) | Argo Rollouts が呼び出す計測 plugin の実行イメージを作ります。 |
| [adapters_test.go](adapters_test.go) | 計測 plugin の HTTP・RPC 接続部分について、実サービスなしで契約を確認します。 |
| [go.mod](go.mod) | 計測 plugin を独立してビルドするための Go module と依存定義です。 |
| [main.go](main.go) | Argo Rollouts からの計測要求を受け、検証したクエリと稼働状態に基づいて結果を返します。 |
| [main_test.go](main_test.go) | 計測 plugin の要求・応答と判定条件を、外部サービスを差し替えて確認します。 |
| [runtime.go](runtime.go) | 計測対象の Pod・版・イメージを照合し、別の配備を誤って合否判定に使わないようにします。 |
