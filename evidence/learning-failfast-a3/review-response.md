# Cloud build block の停止境界修正

対象: 旧head `c9f674219d58d3f4951ac1854bd9e0da568ac7d5` の独立Review blocking finding。
修正: `4ce04c9765a1d2971e5dba00cbad056ff4c2a37a`。

| 条件 | 旧blockの模擬実測 | 修正後の模擬実測 |
| --- | --- | --- |
| source HEAD不一致 | exit 0、tag/saveへ継続 | 非0、build前に停止 |
| dirty checkout | exit 0、tag/saveへ継続 | 非0、build前に停止 |
| image ID不一致 | exit 0、tag/saveへ継続 | 非0、inspect後に停止、tag/saveなし |
| 正常な固定入力 | 対象外 | exit 0、build/inspect/tag/saveを順に実行 |

実README blockへ `set -euo pipefail` を追加。回帰検証は実blockを抽出し、
一時directoryの模擬Git/Dockerだけを使用する。実build・API・Kubernetes操作なし。
詳細の検証結果とsource pinsは report-a3.txt。独立Reviewの修正後判定は未取得。

以前のprivate bundle取得はManager報告によるdispatch failureとして保持する。
この公開Git配送の成立からLibrary取得成功やConsumer利用承認を推論しない。
