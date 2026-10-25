# Local environment values

各Chartの非秘密なlocal差分を配置します。image digestはFrontend／Backendの初回publish後に登録し、Secret値は保存しません。

## 構成と入口

同じ名前の Chart に対応する YAML を上書き値として渡します。ここにあるだけでは配備は実行されません。


主なファイルの入口です。

| ファイル | 役割 |
| --- | --- |
| [order-postgresql.yaml](order-postgresql.yaml) | ローカル環境でOrder 専用 DB の opt-in 配備に使う、Chart の上書き設定です。 |
| [order.yaml](order.yaml) | ローカル環境でOrder API の opt-in 配備に使う、Chart の上書き設定です。 |
