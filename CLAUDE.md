# application-manifest

## 目的
アプリと専用 DB の配備定義を持つ repo。Helm の入力から Kubernetes のリソースを作り、CI で由来と構成を確認する。

## 構成
- `charts/`: リソースを生成する Helm Chart。環境ごとの差は values で渡す
- `environments/`: 環境ごとの Chart 上書き値（local）
- `ci/`: 検査の手順と境界テスト。GitHub から呼ぶ処理は `ci/github/`
- `security/`: 検査の合否条件と schema
- `tools/`: CI から呼ぶ Go ツールと共有ツールの手順
- `learning/`: 学習・検証用の定義と補助ツール（account-release など）
- `docs/`: 設計上の約束、検証結果、運用・引き継ぎ。過去の記録と現在の手順を区別して読む

Argo CD の Application は platform-gitops が持つ。この repo では変えない。

## コマンド
今の CI（`.github/workflows/ci.yml` の validate）が流しているものから、ローカルで足りるもの。ホストに helm が要る。

```text
python3 -I ci/reference_graph_test.py
python3 -I ci/values_contract_test.py
python3 -I ci/trusted_render_test.py -v
python3 -I ci/github/release_test.py
python3 -m unittest discover -s learning/account-release -p 'test_contract.py' -v
```

`ci/values_contract_test.py` は CI では `HELM_BIN=ci/helm-container.sh` を付ける（docker が要る）。ローカルでは付けず、ホストの helm を使う。

次は CI か人が実行する。
- `sh ci/validate.sh`: 34 行目で `.build/inventory` を使う。これは `ci/github/test.sh` の go build で作られ、ホストに helm も要る。
- `bash ci/github/test.sh`、`bash ci/github/source-scan.sh`、`ci/helm-container.sh`: docker が要る。
- `ci/namespace_boundary_test.py`: `unshare --net` を使う。CI では SYS_ADMIN の docker の中で動かす。
- `ci/order_inventory_test.py`: `.rendered` と `.build/order-inventory` が要り、どちらも docker で作る。

learning/account-release のほかの unittest は CI の validate の手順に従う（venv が要る）。

## 変更の流れ
1. main から `feat|fix|chore|docs/PLAT-番号-短い名前` のブランチを切る。
2. 変更し、上のコマンドで確認する。
3. コミットし、push して PR を作る。タイトルは「PLAT-番号: 要約」。
4. PR 本文は「何を／なぜ／どう確かめたか／学び」の 4 見出し。実行したコマンドと出力を貼る。
5. CI の `ci-result` と `pr-conventions` が success になってから、人が merge する。

## 守ること
- main へ push しない。force push、`gh pr merge` もしない（`.claude/settings.json` の deny）。
- 資格情報（`~/.ssh`、`~/.config/gh`、`.env`、`*.pem`、`*.key`）を読まない。値を PR・ログに出さない。
- docker、kubectl、argocd、make は許可していない。必要なら人に頼む。
- 実行できなかった確認は「未確認」と書き、理由を添える。
- 版番号は公式のリリース一覧で確かめてから固定する。
- `.github/workflows/claude-review.yml`、`pr-conventions.yml`、`.github/PULL_REQUEST_TEMPLATE.md`、`CODEOWNERS` は ai-development-system の `templates/` と同じ内容。直すときは先にひな形を直す。
- CODEOWNERS の `/bootstrap/`、`/gitops/root/`、`/scripts/seed-secrets.sh` はこの repo にないパス。
