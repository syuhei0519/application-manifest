#!/usr/bin/env bash
set -euo pipefail
mkdir -p .security .build
sh ci/test-docs-check.sh
for module in tools/*/go.mod; do
  dir=${module%/go.mod}
  # These tests execute Helm; compile them here and run in the pinned Helm container.
  if test "$dir" = tools/checksum; then continue; fi
  (cd "$dir"; go test ./...)
done
(cd tools/scan-gate; CGO_ENABLED=0 go build -trimpath -o ../../.security/scan-gate .)
if test -d tools/oci-input; then
  go test ci/manifest-update.go ci/manifest-update_test.go
  (cd tools/oci-input
   for name in oci-check oci-crane-check github-oci-check; do
     CGO_ENABLED=0 go build -trimpath -o "../../.security/$name" "./cmd/$name"
   done)
  (cd tools/release-record
   for name in sbom-check scan-record publication-check registry-reuse-check registry-origin; do
     CGO_ENABLED=0 go build -trimpath -o "../../.security/$name" "./cmd/$name"
   done)
  for name in oci-budget image-scan-finalizer oci-publication oci-reuse registry-runtime-handoff; do
    sh "ci/test-$name.sh"
  done
elif test -d tools/checksum; then
  (cd tools/checksum; CGO_ENABLED=0 go test -c -o ../../.build/checksum-test .)
  for name in trusted_identity trusted_contract trusted_registry pre_merge_check trusted_attempts; do
    python3 -I "ci/${name}_test.py" -v
  done
  # Historical Linux-userns boundary fixture, isolated from checkout credentials.
  docker run --rm --network=none --memory=1g --cpus=2 --pids-limit=64 \
    --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
    -v "$PWD/ci:/ci:ro" --entrypoint python3 \
    golang:1.27.1-bookworm@sha256:648f440f42a0958804efb24df176f806f9d353b41f1c0627f666428e40310f6b \
    -I /ci/namespace_boundary_test.py -v
else
  (cd tools/bootstrap; CGO_ENABLED=0 go test -c -o ../../.build/bootstrap-test .)
fi
if test -f ci/github/release_test.py; then python3 -I ci/github/release_test.py; fi
