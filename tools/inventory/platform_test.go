package main

import (
	"strings"
	"testing"
)

const platformFixture = `apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: order-postgresql
  annotations:
    argocd.argoproj.io/sync-options: Prune=confirm,Delete=confirm
spec:
  template:
    spec:
      automountServiceAccountToken: false
      securityContext: {runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, fsGroup: 999, seccompProfile: {type: RuntimeDefault}}
      containers:
      - name: order-postgresql
        image: docker.io/library/postgres:17.10-bookworm@sha256:9b18b78397054fce88a9552e9d5a3ad5bb7fd258c5b3cc1c5028e46373d6ea8f
        securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: [ALL]}}
      volumes:
      - name: data
        persistentVolumeClaim: {claimName: order-postgresql-data}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: order-postgresql-data
  annotations:
    argocd.argoproj.io/sync-options: Prune=false,Delete=false
`

func TestPlatformPolicy(t *testing.T) {
	for _, input := range []string{platformFixture, strings.ReplaceAll(platformFixture, "order-postgresql", "postgresql")} {
		if _, err := inventory([]byte(input), "account"); err != nil {
			t.Fatal(err)
		}
	}
	cases := [][2]string{{"17.10-bookworm", "latest"}, {postgresDigest, "sha256:" + strings.Repeat("0", 64)},
		{"docker.io/library/postgres", "ghcr.io/owner/backend-app-order"}, {"runAsNonRoot: true", "runAsNonRoot: false"},
		{"runAsUser: 999", "runAsUser: 0"}, {"RuntimeDefault", "Unconfined"}, {"allowPrivilegeEscalation: false", "allowPrivilegeEscalation: true"},
		{"readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"}, {"drop: [ALL]", "drop: [NET_RAW]"},
		{"automountServiceAccountToken: false", "automountServiceAccountToken: true"}, {"Prune=false,Delete=false", "Prune=true,Delete=true"},
		{"Prune=confirm,Delete=confirm", "Prune=false,Delete=false"}, {"claimName: order-postgresql-data", "claimName: postgresql-data"},
		{"volumes:", "hostNetwork: true\n      volumes:"}}
	for _, change := range cases {
		t.Run(change[0], func(t *testing.T) {
			if _, err := inventory([]byte(strings.Replace(platformFixture, change[0], change[1], 1)), "account"); err == nil {
				t.Fatal("accepted unsafe platform")
			}
		})
	}
}
func TestUnscannedPodSpecs(t *testing.T) {
	for _, input := range []string{
		fixture("Pod") + "other:\n  containers:\n  - name: hidden\n    image: " + img + "\n",
		fixture("Pod") + "---\napiVersion: v1\nkind: ConfigMap\nmetadata: {name: hidden}\nspec:\n  containers:\n  - name: hidden\n    image: " + img + "\n",
	} {
		if _, err := inventory([]byte(input), "account"); err == nil {
			t.Fatal("accepted hidden PodSpec")
		}
	}
}
