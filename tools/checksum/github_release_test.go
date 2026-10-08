package checksum

import (
	"gopkg.in/yaml.v3"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestGitHubReleaseAnnotationBoundary(t *testing.T) {
	helm := os.Getenv("HELM")
	if helm == "" {
		helm = "helm"
	}
	for _, app := range []string{"backend", "frontend"} {
		for _, bad := range []string{"", "recordSha256", "sourceRepository", "runAttempt"} {
			t.Run(app+"/"+bad, func(t *testing.T) {
				data, err := os.ReadFile("../../environments/local/" + app + ".yaml")
				if err != nil {
					t.Fatal(err)
				}
				var values map[string]any
				if err = yaml.Unmarshal(data, &values); err != nil {
					t.Fatal(err)
				}
				commit := strings.Repeat("a", 40)
				base := "https://github.com/syuhei0519/" + app + "-app/releases/download/gha-" + commit + "-123-1/"
				release := map[string]any{"schemaVersion": "github-v1", "sourceRepository": "syuhei0519/" + app + "-app", "sourceCommit": commit,
					"runId": "123", "runAttempt": "1", "recordUrl": base + "release-record.json", "recordSha256": strings.Repeat("b", 64),
					"sbomUrl": base + "sbom.cdx.json", "sbomSha256": strings.Repeat("c", 64)}
				if bad == "recordSha256" {
					delete(release, bad)
				}
				if bad == "sourceRepository" {
					release[bad] = "other-owner/" + app + "-app"
				}
				if bad == "runAttempt" {
					release[bad] = "0"
				}
				values["release"] = release
				values["image"] = map[string]any{"repository": "ghcr.io/syuhei0519/" + app + "-app", "tag": commit, "digest": "sha256:" + strings.Repeat("d", 64)}
				data, err = yaml.Marshal(values)
				if err != nil {
					t.Fatal(err)
				}
				path := filepath.Join(t.TempDir(), "native.yaml")
				if err = os.WriteFile(path, data, 0600); err != nil {
					t.Fatal(err)
				}
				out, err := exec.Command(helm, "template", app, "../../charts/"+app, "-f", path).CombinedOutput()
				if bad != "" {
					if err == nil {
						t.Fatal("invalid native release rendered")
					}
					return
				}
				if err != nil {
					t.Fatalf("native release rejected: %v %s", err, out)
				}
				for _, annotation := range []string{"release-schema-version: \"github-v1\"", "source-repository: \"syuhei0519/" + app + "-app\"", "github-run-id: \"123\"", "github-run-attempt: \"1\""} {
					if !strings.Contains(string(out), "account.lab/"+annotation) {
						t.Fatal("native annotation missing")
					}
				}
				for _, legacy := range []string{"source-project-id:", "pipeline-id:", "build-job-id:", "scan-job-id:"} {
					if strings.Contains(string(out), "account.lab/"+legacy) {
						t.Fatal("GitLab provenance mixed into GitHub release")
					}
				}
			})
		}
	}
}
