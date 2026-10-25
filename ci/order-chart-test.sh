#!/usr/bin/env bash
# Order と専用 PostgreSQL の Chart を描画し、無効時の出力や不正な設定の拒否を確認します。
# Synthetic render only. Does not authenticate a release or contact Kubernetes.
set -euo pipefail
mkdir -p .build .rendered/synthetic-order .security/public
(cd tools/inventory; CGO_ENABLED=0 go build -trimpath -o ../../.build/order-inventory .)
docker run --rm --network=none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD:/work" -w /work --entrypoint sh \
  alpine/helm:4.2.4@sha256:76c375eed56144c68d6197c55bc5a4552fb42002190b796729901cbab3ae6e51 -ec '
  for chart in order order-postgresql; do
    fixture=enabled.yaml
    if test "$chart" = order-postgresql; then fixture=postgresql-enabled.yaml; fi
    helm lint --strict "charts/$chart" -f "environments/local/$chart.yaml"
    helm template "$chart" "charts/$chart" -f "environments/local/$chart.yaml" > /tmp/disabled
    grep -q "^kind: ConfigMap$" /tmp/disabled
    grep -q "platform.lab/profile-state: disabled" /tmp/disabled
    ! grep -Eq "^kind: (Deployment|StatefulSet|Job)$" /tmp/disabled
    helm lint --strict "charts/$chart" -f "ci/fixtures/order/$fixture"
    helm template "$chart" "charts/$chart" --namespace account -f "ci/fixtures/order/$fixture" > ".rendered/synthetic-order/$chart.yaml"
  done
  cat .rendered/synthetic-order/order.yaml .rendered/synthetic-order/order-postgresql.yaml > /tmp/order-all.yaml
  .build/order-inventory < /tmp/order-all.yaml > .security/public/order-synthetic-inventory.json
  .security/scan-gate -check-render-only .rendered/synthetic-order -out .security/public/order-synthetic-security.json
  '
# Same strict Kubernetes schema version as the existing CI; fixtures are not live values.
docker run --rm -v "$PWD:/work:ro" -w /work \
  ghcr.io/yannh/kubeconform:v0.7.0-alpine@sha256:8f0eeaaa96ba27ba1500b0e4b1c215acc358d159c62a7ecae58d7a03403287b0 \
  -strict -summary -kubernetes-version 1.36.0 .rendered/synthetic-order
python3 -I ci/order_inventory_test.py
