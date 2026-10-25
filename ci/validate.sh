#!/usr/bin/env sh
# CI で Chart を描画し、入力制約・checksum・許可したリソースとイメージの構成を検査する入口です。
# 非成功終了は後続の検査を止める。成功してもcluster変更・merge権限を自動付与するものではない。
set -eu

mkdir -p .rendered
python3 -I learning/fresh-profile/test_credential_contract.py -v
python3 -I ci/reference_graph_test.py
python3 -I ci/values_contract_test.py
# Complete fixed inventory: no PR-controlled list can remove an input from CI.
charts='frontend backend postgresql'
for path in charts/*/; do
  case "$path" in charts/frontend/|charts/backend/|charts/postgresql/|charts/order/|charts/order-postgresql/) ;; *) echo 'unknown chart' >&2; exit 1 ;; esac
done
for path in environments/local/*.yaml; do
  case "$path" in environments/local/frontend.yaml|environments/local/backend.yaml|environments/local/postgresql.yaml|environments/local/order.yaml|environments/local/order-postgresql.yaml) ;; *) echo 'unknown values' >&2; exit 1 ;; esac
done
for chart in order order-postgresql; do
  if test -e "charts/$chart" || test -e "environments/local/$chart.yaml"; then
    test -f "charts/$chart/Chart.yaml" && test -f "environments/local/$chart.yaml"
    charts="$charts $chart"
  fi
done
# Exclude stale output from previous fixture runs and all.yaml self-concatenation.
rm -f .rendered/*.yaml
for chart in $charts; do
  helm lint --strict "charts/$chart" -f "environments/local/$chart.yaml"
  helm template "$chart" "charts/$chart" -f "environments/local/$chart.yaml" > ".rendered/$chart.yaml"
done

# The adopted Account profile is deliberately still flat and digest-only. Run
# the same fixed inventory binary used by trusted PR verification so CI proves
# that bounded compatibility path before a proposal is evaluated.
helm lint --strict learning/account-release/chart -f learning/account-release/runtime-profile.yaml
helm template account-release learning/account-release/chart --namespace learning-account-release \
  -f learning/account-release/runtime-profile.yaml > .rendered/account-release-current.yaml
.build/inventory --namespace learning-account-release < .rendered/account-release-current.yaml \
  > .rendered/account-release-current.inventory.json

for chart in $charts; do cat ".rendered/$chart.yaml"; done > .rendered/all.yaml
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
