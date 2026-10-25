#!/usr/bin/env sh
set -eu

# Exact Helm supply for host-side Python contract tests. The repository is
# mounted read-only and Helm has no network access.
exec docker run --rm --network=none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD:/work:ro" -w /work --entrypoint helm \
  alpine/helm:4.2.4@sha256:76c375eed56144c68d6197c55bc5a4552fb42002190b796729901cbab3ae6e51 \
  "$@"
