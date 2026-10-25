# Charts

このディレクトリには`frontend`、`backend`、`postgresql`の具体的な独立Chartを配置します。umbrella Chart、共通Generic Chart、Secret生成テンプレートは置きません。

## 構成と入口

各 Chart の Chart.yaml が識別情報、values.yaml が既定値、templates が出力を作る処理です。values.schema.json がある Chart では入力形式も検証します。

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [backend/](backend) | Account API と migrationを描画する Chart。 |
| [frontend/](frontend) | Account のブラウザー画面を描画する Chart。 |
| [order/](order) | Order API と migrationを描画する Chart。 |
| [order-postgresql/](order-postgresql) | Order 専用 PostgreSQLを描画する Chart。 |
| [postgresql/](postgresql) | Account 専用 PostgreSQLを描画する Chart。 |

### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [backend/values.schema.json](backend/values.schema.json) | Account API と migrationに渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [frontend/values.schema.json](frontend/values.schema.json) | Account のブラウザー画面に渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [order-postgresql/values.schema.json](order-postgresql/values.schema.json) | Order 専用 PostgreSQLに渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [order/values.schema.json](order/values.schema.json) | Order API と migrationに渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [postgresql/values.schema.json](postgresql/values.schema.json) | Account 専用 PostgreSQLに渡す設定値の必須項目・型・許可する値を検証する schema です。 |
