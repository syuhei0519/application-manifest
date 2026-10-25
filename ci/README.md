# ci の見方

検査の実行手順と境界テスト。GitHub から呼ぶ処理は github にまとめます。

## 構成と入口

呼び出し順は [github/test.sh](github/test.sh)、イベント条件は [CI 定義](../.github/workflows/ci.yml) を参照します。

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [fixtures/](fixtures) | 検査条件を固定して再現する試験入力。生成結果と同様に、説明目的では書き換えません。 |
| [github/](github) | GitHub の比較元判定、検査呼び出し、公開証跡の処理。 |

### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [github/order-release.schema.json](github/order-release.schema.json) | Order の公開証跡を受け取るときに必要な項目と値の形式を定義します。 |
