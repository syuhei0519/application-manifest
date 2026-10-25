#!/usr/bin/env bash
# GitHub CI で実行する単体テストと境界検査を順に呼び出す入口です。
set -euo pipefail
mkdir -p .security .build
sh ci/test-docs-check.sh
for module in tools/*/go.mod; do
  dir=${module%/go.mod}
  if test "$dir" = tools/checksum; then continue; fi
  (cd "$dir"; go test ./...)
done
(cd tools/inventory; CGO_ENABLED=0 go build -trimpath -o ../../.build/inventory .)
(cd tools/scan-gate; CGO_ENABLED=0 go build -trimpath -o ../../.security/scan-gate .)
(cd tools/checksum; CGO_ENABLED=0 go test -c -o ../../.build/checksum-test .)
python3 -I ci/trusted_render_test.py -v
# The data-only render sandbox stays networkless and credential-free.
docker run --rm --network=none --memory=1g --cpus=2 --pids-limit=64 \
  --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --cap-add=SYS_ADMIN \
  -v "$PWD/ci:/ci:ro" --entrypoint python3 \
  golang:1.27.1-bookworm@sha256:648f440f42a0958804efb24df176f806f9d353b41f1c0627f666428e40310f6b \
  -I /ci/namespace_boundary_test.py -v
bash ci/order-chart-test.sh
if test -f ci/github/release_test.py; then python3 -I ci/github/release_test.py; fi
