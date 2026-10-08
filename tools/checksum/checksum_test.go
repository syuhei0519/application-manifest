// Helmで設定変更/無関係変更をrender比較する回帰試験。
// ConfigMap変更でchecksum/PodTemplateが変わり、Serviceだけの変更では変わらないことを確認する。実Pod rolloutは行わない。
package checksum

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"os/exec"
	"reflect"
	"testing"

	"gopkg.in/yaml.v3"
)

func deployment(t *testing.T, service string, overrides ...string) (string, any) {
	t.Helper()
	helm := os.Getenv("HELM")
	if helm == "" {
		helm = "helm"
	}
	args := []string{"template", service, "../../charts/" + service, "-f", "../../environments/local/" + service + ".yaml"}
	for _, v := range overrides {
		args = append(args, "--set", v)
	}
	data, err := exec.Command(helm, args...).CombinedOutput()
	if err != nil {
		t.Fatalf("helm: %v %s", err, data)
	}
	decoder := yaml.NewDecoder(bytes.NewReader(data))
	for {
		var resource map[string]any
		err := decoder.Decode(&resource)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if resource["kind"] == "Secret" {
			t.Fatal("chart generated Secret")
		}
		if resource["kind"] != "Deployment" {
			continue
		}
		template := resource["spec"].(map[string]any)["template"].(map[string]any)
		checksum := template["metadata"].(map[string]any)["annotations"].(map[string]any)["checksum/config"].(string)
		if len(checksum) != 64 {
			t.Fatal("invalid checksum")
		}
		return checksum, template
	}
	t.Fatal("Deployment missing")
	return "", nil
}

func TestConfigChangesRollPodTemplate(t *testing.T) {
	cases := map[string][]string{
		"frontend": {"config.apiBasePath=/fixture-api", "config.apiUpstream=http://fixture-backend:8080"},
		"backend":  {"config.logLevel=debug", "config.dbHost=fixture-db", "config.dbPort=5433", "config.dbName=fixture", "config.sslmode=require"},
	}
	baselines := map[string][]string{
		"frontend": {"config.apiBasePath=/api", "config.apiUpstream=http://backend.account.svc.cluster.local:8080"},
		"backend":  {"config.logLevel=info", "config.dbHost=postgresql", "config.dbPort=5432", "config.dbName=account", "config.sslmode=disable"},
	}
	for service, overrides := range cases {
		t.Run(service, func(t *testing.T) {
			oldChecksum, oldTemplate := deployment(t, service, baselines[service]...)
			for _, override := range overrides {
				t.Run(override, func(t *testing.T) {
					checksum, template := deployment(t, service, append(append([]string{}, baselines[service]...), override)...)
					if checksum == oldChecksum || reflect.DeepEqual(template, oldTemplate) {
						t.Fatalf("%s did not change checksum and PodTemplate", override)
					}
					t.Logf("%s checksum %s -> %s", override, oldChecksum, checksum)
				})
			}
			checksum, template := deployment(t, service, append(append([]string{}, baselines[service]...), "service.port=18080")...)
			if checksum != oldChecksum || !reflect.DeepEqual(template, oldTemplate) {
				t.Fatal("unrelated Service port rolled PodTemplate")
			}
			// Secret参照変更でrolloutが必要になる場合も、ConfigMap checksumへSecretを含めてはいけない。Chart入出力にSecret実値は存在しない。
			if service == "backend" {
				checksum, _ = deployment(t, service, append(append([]string{}, baselines[service]...), "secrets.existingSecret=fixture-secret")...)
				if checksum != oldChecksum {
					t.Fatal("Secret reference contaminated config checksum")
				}
			}
			fmt.Println(service, "unrelated Service port preserves PodTemplate")
		})
	}
}
