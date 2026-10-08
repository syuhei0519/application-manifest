// backend Deploymentとmigrationの検査済みrelease注釈をrender確認する。
// 必要checksum欠落・project不一致・旧pipeline注釈混在を拒否する境界を守る。
package checksum

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func TestBackendInspectedReleaseAnnotations(t *testing.T) {
	helm := os.Getenv("HELM")
	if helm == "" {
		helm = "helm"
	}
	release := map[string]any{
		"schemaVersion": "2", "sourceProjectId": "86247033",
		"sourceCommit": strings.Repeat("a", 40), "buildPipelineId": "1", "buildJobId": "2",
		"scanPipelineId": "3", "scanJobId": "4", "recordUrl": "https://example.invalid/record.json",
		"recordSha256": strings.Repeat("b", 64), "sbomUrl": "https://example.invalid/sbom.json",
		"sbomSha256": strings.Repeat("c", 64), "pipelineId": nil, "pipelineUrl": nil,
	}
	for _, bad := range []string{"", "sourceProjectId", "sbomSha256"} {
		candidate := map[string]any{}
		for key, value := range release {
			candidate[key] = value
		}
		if bad == "sourceProjectId" {
			candidate[bad] = "86247025"
		}
		if bad == "sbomSha256" {
			candidate[bad] = nil // Explicit null removes an inherited value during Helm coalescing.
		}
		// The deployed release may use GitHub provenance. Keep this historical
		// GitLab contract fixture independent of that selected provider.
		data, err := os.ReadFile("../../environments/local/backend.yaml")
		if err != nil {
			t.Fatal(err)
		}
		var values map[string]any
		if err := yaml.Unmarshal(data, &values); err != nil {
			t.Fatal(err)
		}
		values["release"] = candidate
		data, err = yaml.Marshal(values)
		if err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(t.TempDir(), "release.yaml")
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
		out, err := exec.Command(helm, "template", "backend", "../../charts/backend", "-f", path).CombinedOutput()
		if bad != "" {
			if err == nil {
				t.Fatalf("invalid %s release rendered", bad)
			}
			continue
		}
		if err != nil {
			t.Fatalf("inspected backend release refused: %v %s", err, out)
		}
		for key, value := range map[string]string{"release-schema-version": "2", "build-pipeline-id": "1", "build-job-id": "2", "scan-pipeline-id": "3", "scan-job-id": "4", "release-record-url": release["recordUrl"].(string), "release-record-sha256": release["recordSha256"].(string), "sbom-url": release["sbomUrl"].(string), "sbom-sha256": release["sbomSha256"].(string)} {
			if !strings.Contains(string(out), "account.lab/"+key+": \""+value+"\"") {
				t.Fatalf("missing bound annotation %s", key)
			}
		}
		if strings.Contains(string(out), "account.lab/pipeline-id:") || strings.Contains(string(out), "account.lab/pipeline-url:") {
			t.Fatal("legacy provenance remained in inspected release")
		}
	}
}
