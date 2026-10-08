#!/usr/bin/env sh
set -eu
umask 077
mkdir -p .security/private .security/public .security/source .cache/trivy
trap 'rm -rf .security/private .security/source' EXIT HUP INT TERM
# A tracked-source archive excludes Git credential config, runner files and
# generated reports/caches from the source-scan input.
git archive --format=tar HEAD > .security/private/source.tar
tar -xf .security/private/source.tar -C .security/source
scanner_exit=0
: > .security/private/empty-ignore
attempt=0
while :; do
  attempt=$((attempt + 1))
  if trivy image --download-db-only --cache-dir .cache/trivy --timeout 5m --quiet > .security/private/db.log 2>&1; then break; fi
  if [ "$attempt" -ge 2 ]; then
    printf '%s\n' '{"pass":false,"reason":"scanner DB retrieval unavailable"}' > .security/public/failure.json
    echo 'Security DB retrieval failed; no delivery permitted' >&2
    exit 2
  fi
  sleep 2
done
db_input=.cache/trivy/db/metadata.json
policy_input=security/scan-policy.json
scan_timeout=5m
# Acceptance injection only makes the fixed production gate stricter/fail.
# The shared cache and production policy are never changed by these cases.
case "${AT11_CASE:-}" in
  '') ;;
  scanner-timeout) scan_timeout=1ns ;;
  stale-db)
    printf '%s\n' '{"Version":2,"UpdatedAt":"2000-01-01T00:00:00Z"}' > .security/private/stale-db.json
    db_input=.security/private/stale-db.json ;;
  expired-exception)
    printf '%s\n' '{"schemaVersion":1,"trivyVersion":"0.75.0","dbMaxAgeHours":24,"exceptions":[{"id":"expired-acceptance","kind":"secret","rule":"fixture-rule","package":"","path":"ci/fixtures/unused.txt","owner":"Lab operator","reason":"AT11 negative acceptance only","createdAt":"2000-01-01T00:00:00Z","expiresAt":"2000-01-02T00:00:00Z"}]}' > .security/private/expired-policy.json
    policy_input=.security/private/expired-policy.json ;;
  *) echo 'Unknown acceptance case; no delivery permitted' >&2; exit 2 ;;
esac
if ! trivy fs --cache-dir .cache/trivy --skip-db-update --timeout "$scan_timeout" --quiet --no-progress --include-dev-deps --scanners vuln,secret,misconfig --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL --ignorefile .security/private/empty-ignore --list-all-pkgs --format json --output .security/private/raw.json .security/source > .security/private/scan.log 2>&1; then
  printf '%s\n' '{"pass":false,"reason":"scanner execution unavailable"}' > .security/public/failure.json
  echo 'Security scanner failed; no delivery permitted' >&2
  exit 2
fi
set -- -policy "$policy_input" -db "$db_input" -report .security/private/raw.json -out .security/public/source-scan.json -scanner-exit "$scanner_exit"
if [ "${SECURITY_RENDER_REQUIRED:-false}" = true ]; then
  test -d .rendered || { echo 'Rendered inputs missing; no delivery permitted' >&2; exit 2; }
  # Scan the actual render artifact as well as tracked source.
  if ! trivy fs --cache-dir .cache/trivy --skip-db-update --timeout 5m --quiet --no-progress --scanners vuln,secret,misconfig --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL --ignorefile .security/private/empty-ignore --format json --output .security/private/render-raw.json .rendered > .security/private/render.log 2>&1; then
    echo 'Rendered scanner failed; no delivery permitted' >&2;exit 2
  fi
  .security/scan-gate -policy "$policy_input" -db "$db_input" -report .security/private/render-raw.json -out .security/public/render-scan.json -scanner-exit 0 -rendered .rendered
fi
if ! .security/scan-gate "$@"; then
  printf '%s\n' '{"pass":false,"reason":"security policy gate rejected scanner inputs or findings"}' > .security/public/failure.json
  exit 1
fi
