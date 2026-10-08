package main

import (
	"strings"
	"testing"
)

const img = "registry.gitlab.com/syuhei-platform-engineering-lab/backend-app:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

func fixture(kind string) string {
	api := "apps/v1"
	spec := "spec:\n  template:\n    metadata:\n      annotations:\n        account.lab/pipeline-id: \"1\"\n    spec:\n      containers:\n      - name: app\n        image: " + img + "\n"
	switch kind {
	case "Pod":
		api = "v1"
		spec = "spec:\n  containers:\n  - name: app\n    image: " + img + "\n"
	case "ReplicationController":
		api = "v1"
	case "Job":
		api = "batch/v1"
	case "CronJob":
		api = "batch/v1"
		spec = "spec:\n  jobTemplate:\n    spec:\n      template:\n        spec:\n          containers:\n          - name: app\n            image: " + img + "\n"
	}
	return "apiVersion: " + api + "\nkind: " + kind + "\nmetadata:\n  name: fixture\n" + spec
}
func TestAllPodSpecKinds(t *testing.T) {
	for _, kind := range []string{"Pod", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "ReplicationController", "Job", "CronJob"} {
		t.Run(kind, func(t *testing.T) {
			out, err := inventory([]byte(fixture(kind)), "account")
			if err != nil {
				t.Fatal(err)
			}
			if len(out.Entries) != 1 || out.Entries[0].Kind != kind {
				t.Fatal("wrong inventory")
			}
		})
	}
}
func TestAT01ChangeDetection(t *testing.T) {
	before := fixture("Deployment")
	baseline, err := inventory([]byte(before), "account")
	if err != nil {
		t.Fatal(err)
	}
	cases := map[string]string{
		"template-or-default-image": strings.Replace(before, img, strings.Replace(img, "bbbb", "cccc", 1), 1),
		"other-repository":          strings.Replace(before, "backend-app:", "other-app:", 1),
		"release-only":              strings.Replace(before, "pipeline-id: \"1\"", "pipeline-id: \"2\"", 1),
		"sidecar":                   before + "      - name: sidecar\n        image: " + img + "\n",
		"init-container":            before + "      initContainers:\n      - name: init\n        image: " + img + "\n",
		"migration-only":            before + "---\n" + fixture("Job"),
	}
	for name, input := range cases {
		t.Run(name, func(t *testing.T) {
			changed, err := inventory([]byte(input), "account")
			if err != nil {
				t.Fatal(err)
			}
			if changed.InventorySHA256 == baseline.InventorySHA256 {
				t.Fatal("change bypassed inventory")
			}
		})
	}
	stable, err := inventory([]byte(before+"      nodeSelector:\n        fixture: config-only\n"), "account")
	if err != nil {
		t.Fatal(err)
	}
	if stable.InventorySHA256 != baseline.InventorySHA256 || stable.RenderSHA256 == baseline.RenderSHA256 {
		t.Fatal("config-only detection incorrect")
	}
}
func TestFailClosed(t *testing.T) {
	inputs := []string{"not: [yaml", fixture("CustomWorkload"), strings.Replace(fixture("Pod"), img, "nginx:latest", 1), fixture("Pod") + "---\n" + fixture("Pod"), strings.Replace(fixture("Pod"), "name: app", "name: ''", 1), "apiVersion: v1\nkind: Secret\nmetadata:\n  name: forbidden\n"}
	for _, input := range inputs {
		if _, err := inventory([]byte(input), "account"); err == nil {
			t.Fatalf("accepted invalid resource %.80s", input)
		}
	}
}
