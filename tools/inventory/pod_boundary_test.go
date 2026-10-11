package main

import (
	"strings"
	"testing"
)

func TestHostNamespaceBoundary(t *testing.T) {
	bases := map[string]string{}
	for _, kind := range []string{"Pod", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "ReplicationController", "Job", "CronJob"} {
		bases[kind] = fixture(kind)
	}
	for _, name := range []string{"postgresql", "order-postgresql"} {
		bases[name] = strings.ReplaceAll(platformFixture, "order-postgresql", name)
	}
	for name, base := range bases {
		for _, field := range []string{"hostNetwork", "hostPID", "hostIPC"} {
			for _, value := range []string{"", "false", "False", "FALSE", "true", "null", "'false'", "'true'", "0", "[]", "{}"} {
				t.Run(name+"/"+field+"/"+value, func(t *testing.T) {
					input := base
					if value != "" {
						lines := strings.Split(base, "\n")
						for i, line := range lines {
							if strings.TrimSpace(line) == "containers:" {
								prefix := line[:len(line)-len(strings.TrimLeft(line, " "))]
								lines[i] = prefix + field + ": " + value + "\n" + line
								break
							}
						}
						input = strings.Join(lines, "\n")
					}
					out, err := inventory([]byte(input), "account")
					allowed := value == "" || strings.EqualFold(value, "false")
					if (err == nil) != allowed {
						t.Fatalf("allowed=%v error=%v", allowed, err)
					}
					if allowed {
						original, e := inventory([]byte(base), "account")
						if e != nil || original.InventorySHA256 != out.InventorySHA256 {
							t.Fatal("safe settings changed inventory")
						}
					}
				})
			}
		}
	}
	// Unsupported and hidden PodSpecs remain fail-closed even with false/omitted host settings.
	for _, v := range []string{"", "hostNetwork: false\n", "hostPID: true\n", "hostIPC: null\n"} {
		for _, kind := range []string{"CustomWorkload", "ConfigMap"} {
			input := "apiVersion: v1\nkind: " + kind + "\nmetadata: {name: hidden}\nspec:\n  " + strings.ReplaceAll(v, "\n", "\n  ") + "containers: [{name: app, image: '" + img + "'}]\n"
			if _, err := inventory([]byte(input), "account"); err == nil {
				t.Fatal("hidden PodSpec accepted")
			}
		}
	}
}

func TestRenderDestinationBoundary(t *testing.T) {
	for _, kind := range []string{"Pod", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "ReplicationController", "Job", "CronJob", "ConfigMap", "Service", "ServiceAccount", "PersistentVolumeClaim", "NetworkPolicy", "PodDisruptionBudget", "HorizontalPodAutoscaler"} {
		for _, ns := range []string{"account", "''", "kube-system", "null", "false", "[]", "{}"} {
			t.Run(kind+"/"+ns, func(t *testing.T) {
				base := fixture(kind)
				switch kind {
				case "ConfigMap", "Service", "ServiceAccount", "PersistentVolumeClaim", "NetworkPolicy", "PodDisruptionBudget", "HorizontalPodAutoscaler":
					api := "v1"
					if kind == "NetworkPolicy" {
						api = "networking.k8s.io/v1"
					}
					if kind == "PodDisruptionBudget" {
						api = "policy/v1"
					}
					if kind == "HorizontalPodAutoscaler" {
						api = "autoscaling/v2"
					}
					base = "apiVersion: " + api + "\nkind: " + kind + "\nmetadata:\n  name: auxiliary\n"
				}
				input := strings.Replace(base, "metadata:\n", "metadata:\n  namespace: "+ns+"\n", 1) + "---\n" + strings.Replace(fixture("Pod"), "name: fixture", "name: companion", 1)
				_, err := inventory([]byte(input), "account")
				if (err == nil) != (ns == "account" || ns == "''") {
					t.Fatalf("unexpected destination result: %v", err)
				}
			})
		}
	}
}
