# 過去の証跡

生成済みの JSON、ログ、重複 render は現行ツリーから整理した。過去の失敗・未実施を成功に変更していない。
以下の固定 commit の元ディレクトリから当時の出力を取得できる。Git 履歴は保持する。

[当時の出力](https://github.com/syuhei0519/application-manifest/tree/c8bad893f660a1b973e528d14d9b299d9b0d8a5f/docs/evidence)

整理対象: 3 ファイル。新しい出力は CI artifact に保存し、再現コマンド・対象 commit・結論を Markdown に記す。

再現は repo root の `bash ci/github/test.sh` と `sh ci/validate.sh`。runtime 検証の結論と制約は関連 docs を参照。
