// Backend の配備と migration Job に、同じ公開証跡の注釈が付くことを各公開形式で確認します。
package checksum

import (
	"bytes"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// 移行 Job とアプリ本体のリリース情報が食い違うと、同じ版を使ったか確認できない。
// 対応する全形式で、両方の Pod に同じリリース注釈が付くことを確認する。
func TestBackendReleaseAnnotationsMatchAcrossWorkloads(t *testing.T) {
	helm := os.Getenv("HELM")
	if helm == "" {
		helm = "helm"
	}
	commit := strings.Repeat("a", 40)
	record := map[string]any{
		"sourceCommit": commit, "recordUrl": "https://example.invalid/record.json",
		"recordSha256": strings.Repeat("b", 64), "sbomUrl": "https://example.invalid/sbom.json", "sbomSha256": strings.Repeat("c", 64),
	}
	github := map[string]any{
		"schemaVersion": "github-v1", "sourceRepository": "syuhei0519/backend-app", "runId": "123", "runAttempt": "1",
	}
	inspected := map[string]any{
		"schemaVersion": "2", "sourceProjectId": "86247033", "buildPipelineId": "1", "buildJobId": "2", "scanPipelineId": "3", "scanJobId": "4",
	}
	for key, value := range record {
		github[key] = value
		inspected[key] = value
	}
	base := "https://github.com/syuhei0519/backend-app/releases/download/gha-" + commit + "-123-1/"
	github["recordUrl"] = base + "release-record.json"
	github["sbomUrl"] = base + "sbom.cdx.json"
	for name, release := range map[string]map[string]any{
		"github":    github,
		"inspected": inspected,
		"legacy":    {"sourceProjectId": "1", "sourceCommit": commit, "pipelineId": "2", "pipelineUrl": "https://example.invalid/pipeline"},
	} {
		t.Run(name, func(t *testing.T) {
			values := map[string]any{
				"release": release,
				"image":   map[string]any{"repository": "ghcr.io/syuhei0519/backend-app", "tag": commit, "digest": "sha256:" + strings.Repeat("d", 64)},
			}
			data, err := yaml.Marshal(values)
			if err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(t.TempDir(), "values.yaml")
			if err = os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			out, err := exec.Command(helm, "template", "backend", "../../charts/backend", "-f", path).CombinedOutput()
			if err != nil {
				t.Fatalf("helm: %v %s", err, out)
			}
			annotations := map[string]map[string]any{}
			decoder := yaml.NewDecoder(bytes.NewReader(out))
			for {
				var resource map[string]any
				if err = decoder.Decode(&resource); err == io.EOF {
					break
				} else if err != nil {
					t.Fatal(err)
				}
				kind, _ := resource["kind"].(string)
				if kind != "Deployment" && kind != "Job" {
					continue
				}
				template := resource["spec"].(map[string]any)["template"].(map[string]any)
				items := template["metadata"].(map[string]any)["annotations"].(map[string]any)
				// 設定変更と明示的な再起動の注釈は Deployment だけが使う。
				delete(items, "checksum/config")
				delete(items, "account.lab/restart-nonce")
				annotations[kind] = items
			}
			if len(annotations) != 2 || len(annotations["Deployment"]) == 0 || !reflect.DeepEqual(annotations["Deployment"], annotations["Job"]) {
				t.Fatalf("release annotations differ: %#v", annotations)
			}
		})
	}
}
