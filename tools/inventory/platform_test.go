// platform の inventory 作成で、許可外リソースやイメージの混入を拒否することを確認します。
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

// Both fixed DB identities must enforce the effective container security, not
// merely the Pod defaults. These are synthetic rendered YAML fixtures.
func TestPlatformContainerOverrides(t *testing.T) {
	for _, name := range []string{"postgresql", "order-postgresql"} {
		base := strings.ReplaceAll(platformFixture, "order-postgresql", name)
		for _, tc := range []struct {
			name, override string
			accept         bool
		}{
			{"inherit", "", true},
			{"explicit-equivalent", "runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, seccompProfile: {type: RuntimeDefault}, ", true},
			{"partial-equivalent", "runAsUser: 999, ", true},
			{"root", "runAsUser: 0, runAsNonRoot: false, ", false},
			{"nonroot-false", "runAsNonRoot: false, ", false},
			{"wrong-uid", "runAsUser: 10001, ", false},
			{"root-gid", "runAsGroup: 0, ", false},
			{"unconfined", "seccompProfile: {type: Unconfined}, ", false},
			{"localhost", "seccompProfile: {type: Localhost, localhostProfile: fixture}, ", false},
			{"empty-seccomp", "seccompProfile: {}, ", false},
			{"null-seccomp", "seccompProfile: null, ", false},
			{"scalar-seccomp", "seccompProfile: RuntimeDefault, ", false},
			{"null-uid", "runAsUser: null, ", false},
			{"string-uid", "runAsUser: '999', ", false},
			{"string-gid", "runAsGroup: '999', ", false},
			{"string-nonroot", "runAsNonRoot: 'true', ", false},
			{"object-uid", "runAsUser: {}, ", false},
		} {
			t.Run(name+"/"+tc.name, func(t *testing.T) {
				input := strings.Replace(base, "securityContext: {allowPrivilegeEscalation:", "securityContext: {"+tc.override+"allowPrivilegeEscalation:", 1)
				_, err := inventory([]byte(input), "account")
				if (err == nil) != tc.accept {
					t.Fatalf("accept=%v, error=%v", tc.accept, err)
				}
			})
		}
	}
}

// The platform allowlist never admitted PostgreSQL init/ephemeral containers or
// extra sidecars. Keep those identities rejected even with safe inherited
// settings; this repair must not widen their allowed roles.
func TestPlatformContainerRoles(t *testing.T) {
	for _, name := range []string{"postgresql", "order-postgresql"} {
		base := strings.ReplaceAll(platformFixture, "order-postgresql", name)
		start := strings.Index(base, "      - name: "+name)
		end := strings.Index(base, "      volumes:")
		container := base[start:end]
		for _, role := range []string{"containers", "initContainers", "ephemeralContainers"} {
			for _, override := range []string{"", "runAsUser: 0, runAsNonRoot: false, "} {
				added := strings.Replace(container, "- name: "+name, "- name: sidecar", 1)
				added = strings.Replace(added, "securityContext: {", "securityContext: {"+override, 1)
				if role != "containers" {
					added = "      " + role + ":\n" + added
				}
				t.Run(name+"/"+role+"/"+override, func(t *testing.T) {
					if _, err := inventory([]byte(base[:end]+added+base[end:]), "account"); err == nil {
						t.Fatal("accepted platform image in an unauthorized container role")
					}
				})
			}
		}
	}
}

// Synthetic image/identity cross-product: neither reserved DB name may escape
// through any image family, and the platform image cannot acquire an app name.
func TestPlatformIdentityBeforeImageFamily(t *testing.T) {
	pgImage := postgresRepository + ":" + postgresTag + "@" + postgresDigest
	appImages := []string{
		"ghcr.io/syuhei0519/backend-app",
		"ghcr.io/syuhei0519/backend-app-order",
		"ghcr.io/syuhei0519/frontend-app",
		"registry.gitlab.com/syuhei-platform-engineering-lab/backend-app",
		"example.invalid/unknown",
	}
	for _, name := range []string{"postgresql", "order-postgresql"} {
		base := strings.ReplaceAll(platformFixture, "order-postgresql", name)
		for _, namespace := range []string{"", "account", "synthetic-other"} {
			input := base
			if namespace != "" {
				input = strings.ReplaceAll(input, "metadata:\n", "metadata:\n  namespace: "+namespace+"\n")
			}
			destination := namespace
			if destination == "" {
				destination = "account"
			}
			t.Run(name+"/"+namespace+"/fixed", func(t *testing.T) {
				out, err := inventory([]byte(input), destination)
				if err != nil || len(out.Entries) != 1 {
					t.Fatalf("fixed identity rejected: %v", err)
				}
			})
			for _, repo := range appImages {
				t.Run(name+"/"+namespace+"/"+repo, func(t *testing.T) {
					image := repo + ":" + strings.Repeat("a", 40) + "@sha256:" + strings.Repeat("b", 64)
					if _, err := inventory([]byte(strings.Replace(input, pgImage, image, 1)), destination); err == nil {
						t.Fatal("DB identity escaped through another image family")
					}
				})
			}
		}
		for _, change := range [][2]string{
			{postgresTag, "17.9-bookworm"},
			{postgresDigest, "sha256:" + strings.Repeat("b", 64)},
			{"name: " + name + "\n", "name: unknown\n"},
			{"- name: " + name + "\n", "- name: unknown\n"},
			{"kind: StatefulSet", "kind: Deployment"},
			{"apiVersion: apps/v1", "apiVersion: unknown/v1"},
			{"      containers:", "      initContainers:"},
			{"      containers:", "      ephemeralContainers:"},
			{"claimName: " + name + "-data", "claimName: unknown-data"},
			{"kind: PersistentVolumeClaim\nmetadata:", "kind: PersistentVolumeClaim\nmetadata:\n  namespace: synthetic-other"},
		} {
			t.Run(name+"/binding/"+change[0], func(t *testing.T) {
				if _, err := inventory([]byte(strings.Replace(base, change[0], change[1], 1)), "account"); err == nil {
					t.Fatal("accepted invalid platform binding")
				}
			})
		}
		// Extra application containers also cannot escape DB policy, regardless
		// of the container list in which they appear.
		for _, role := range []string{"containers", "initContainers", "ephemeralContainers"} {
			added := "      - name: app\n        image: " + appImages[0] + ":" + strings.Repeat("a", 40) + "@sha256:" + strings.Repeat("b", 64) + "\n"
			if role != "containers" {
				added = "      " + role + ":\n" + added
			}
			t.Run(name+"/"+role+"/app-sidecar", func(t *testing.T) {
				input := strings.Replace(base, "      volumes:", added+"      volumes:", 1)
				if _, err := inventory([]byte(input), "account"); err == nil {
					t.Fatal("accepted application sidecar on fixed DB")
				}
			})
		}
	}
	for _, kind := range []string{"Pod", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "ReplicationController", "Job", "CronJob"} {
		for _, repo := range appImages {
			image := repo + ":" + strings.Repeat("a", 40) + "@sha256:" + strings.Repeat("b", 64)
			base := strings.Replace(fixture(kind), img, image, 1)
			t.Run(kind+"/"+repo+"/app", func(t *testing.T) {
				if _, err := inventory([]byte(base), "account"); err != nil {
					t.Fatalf("ordinary app handling changed: %v", err)
				}
			})
			for _, name := range []string{"postgresql", "order-postgresql"} {
				t.Run(kind+"/"+repo+"/"+name, func(t *testing.T) {
					input := strings.Replace(base, "name: fixture", "name: "+name, 1)
					if _, err := inventory([]byte(input), "account"); err == nil {
						t.Fatal("reserved DB workload entered app inventory")
					}
				})
			}
		}
		t.Run(kind+"/platform-under-app-identity", func(t *testing.T) {
			if _, err := inventory([]byte(strings.Replace(fixture(kind), img, pgImage, 1)), "account"); err == nil {
				t.Fatal("platform image accepted under app identity")
			}
		})
	}
}
