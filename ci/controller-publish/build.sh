#!/usr/bin/env bash
set -euo pipefail

readonly root="$PWD"
readonly epoch=1793248136
readonly binary=02cdb9d92d5570c1fbb9cdebf4b354222c05be053ebbf7b84b80b443d68beacb
readonly archive=bd5a2f85d4a6301b84247ebc61d2eabdeaa0929165303943b6dc814924ec85e8
readonly buildkit=moby/buildkit@sha256:6eceb8971ce4fceb3daca562832642706238b7eea72941fcf9896c93c3c4a53e
readonly trivy=aquasec/trivy:0.75.0@sha256:9db099105405c648166e6b94155eb32f8da12673cf1f455207f7385cc9a77283
test "$(git -C .source rev-parse HEAD)" = c879ac072bf78ba33142070932e86503cb7242b8
test "$(git -C .source show -s --format=%ct HEAD)" = "$epoch"
test "$(sha256sum security/scan-policy.json | cut -d' ' -f1)" = d10f26ad064971e74b94f821560ede5303f0bcf4d364ea2514c45faac4c27337
mkdir -p .publish .private .bin .cache/trivy
python3 ci/controller-publish/make_proxy.py .source "$root/.private/proxy" > .publish/provenance.json
(
  cd .source/learning/account-release/metric-plugin
  go test ./...
)
for context in a b; do
  mkdir -p ".private/context-$context" ".private/bin-$context" ".private/go-cache-$context"
  env GOBIN="$root/.private/bin-$context" GOCACHE="$root/.private/go-cache-$context" \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOTOOLCHAIN=local \
    GOPROXY="file://$root/.private/proxy,https://proxy.golang.org" \
    GONOSUMDB=learning.local/account-release-metric \
    go install -trimpath -buildvcs=false learning.local/account-release-metric@v0.0.0-20261029042856-c879ac072bf7
  test "$(sha256sum ".private/bin-$context/account-release-metric" | cut -d' ' -f1)" = "$binary"
  cp ".private/bin-$context/account-release-metric" ".private/context-$context/account-policy"
  cp .source/learning/account-release/metric-plugin/Dockerfile ".private/context-$context/Dockerfile"
  touch -d "@$epoch" ".private/context-$context/account-policy" ".private/context-$context/Dockerfile"
done
docker buildx create --name account-repro-second --driver docker-container --driver-opt "image=$buildkit"
trap 'docker buildx rm account-repro-second >/dev/null 2>&1 || true' EXIT
for context in a b; do
  builder=${ACCOUNT_BUILDER:?builder name required}
  if [ "$context" = b ]; then builder=account-repro-second; fi
  docker buildx build --builder "$builder" --no-cache --network=none --platform linux/amd64 \
    --build-arg "SOURCE_DATE_EPOCH=$epoch" --provenance=false \
    --output "type=oci,dest=$root/.publish/$context.oci.tar,rewrite-timestamp=true" ".private/context-$context"
  test "$(sha256sum ".publish/$context.oci.tar" | cut -d' ' -f1)" = "$archive"
done
cmp .publish/a.oci.tar .publish/b.oci.tar
mv .publish/a.oci.tar .publish/controller.oci.tar
rm .publish/b.oci.tar
python3 ci/controller-publish/verify_oci.py .publish/controller.oci.tar .publish/.oci/layout
(cd tools/scan-gate; go build -trimpath -o "$root/.bin/scan-gate" .)
(cd tools/release-record; go build -trimpath -o "$root/.bin/sbom-check" ./cmd/sbom-check)
docker run --rm --user "$(id -u):$(id -g)" -v "$root/.cache/trivy:/cache" "$trivy" \
  --cache-dir /cache image --download-db-only --db-repository ghcr.io/aquasecurity/trivy-db:2
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$root/.cache/trivy:/cache" -v "$root/.publish:/work" -w /work "$trivy" \
  --cache-dir /cache image --skip-db-update --skip-java-db-update --skip-check-update --offline-scan \
  --ignorefile '' --scanners vuln,secret,misconfig --list-all-pkgs --format json --output /work/raw.json --input .oci/layout
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$root/.cache/trivy:/cache" -v "$root/.publish:/work" -w /work "$trivy" \
  --cache-dir /cache image --skip-db-update --skip-java-db-update --offline-scan \
  --format cyclonedx --output /work/sbom.cdx.json --input .oci/layout
.bin/scan-gate -policy security/scan-policy.json -db .cache/trivy/db/metadata.json -report .publish/raw.json \
  -out .publish/scan-summary.json -scanner-exit 0 -image-config sha256:08dd8a1ea8d94fde98319f3d3e40bb3ed4a3565b2724b8e08a150e09662e448e
.bin/sbom-check .publish/sbom.cdx.json .publish/raw.json sha256:08dd8a1ea8d94fde98319f3d3e40bb3ed4a3565b2724b8e08a150e09662e448e > .publish/sbom-proof.json
