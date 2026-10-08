#!/usr/bin/env sh
# CI/運用検証の入口。.gitlab-ci.ymlが指定する固定入力と検証器を使用する。
# 非成功終了は後続の検査を止める。成功してもcluster変更・merge権限を自動付与するものではない。
set -eu

mkdir -p .rendered
for chart in frontend backend postgresql; do
  helm lint --strict "charts/$chart" -f "environments/local/$chart.yaml"
  helm template "$chart" "charts/$chart" -f "environments/local/$chart.yaml" > ".rendered/$chart.yaml"
done

cat .rendered/*.yaml > .rendered/all.yaml
if grep -Eq '^kind: (Namespace|Secret|ClusterRole|ClusterRoleBinding)$' .rendered/all.yaml; then
  echo 'forbidden resource kind rendered' >&2
  exit 1
fi
if grep -Eq 'image: .*:latest(@|$)' .rendered/all.yaml; then
  echo 'floating latest image rendered' >&2
  exit 1
fi
grep -Eq 'image: ".*:[0-9a-f]{40}@sha256:[0-9a-f]{64}"' .rendered/frontend.yaml
grep -Eq 'proxy_pass http://backend\.account\.svc\.cluster\.local:8080(/api/)?;' .rendered/frontend.yaml
! grep -q '^    resolver ' .rendered/frontend.yaml
grep -Eq 'image: ".*:[0-9a-f]{40}@sha256:[0-9a-f]{64}"' .rendered/backend.yaml
test "$(grep -c 'image: ".*backend.*@sha256:' .rendered/backend.yaml)" -eq 2
grep -q 'argocd.argoproj.io/hook: Sync' .rendered/backend.yaml
grep -q 'argocd.argoproj.io/hook-delete-policy: BeforeHookCreation,HookSucceeded' .rendered/backend.yaml
! grep -q 'argocd.argoproj.io/hook: PreSync' .rendered/backend.yaml
grep -q 'claimName: postgresql-data' .rendered/postgresql.yaml
! grep -q 'volumeClaimTemplates:' .rendered/postgresql.yaml
grep -q 'argocd.argoproj.io/sync-options: Prune=false,Delete=false' .rendered/postgresql.yaml
grep -q 'argocd.argoproj.io/sync-options: Prune=confirm,Delete=confirm' .rendered/postgresql.yaml
grep -Eq 'db-host: "(postgresql|postgresql\.account\.svc\.cluster\.local)"' .rendered/backend.yaml
grep -q 'db-name: "account"' .rendered/backend.yaml
grep -q 'POSTGRES_DB' .rendered/postgresql.yaml
