// Helmで設定変更/無関係変更をrender比較する回帰試験。
// ConfigMap変更でchecksum/PodTemplateが変わり、Serviceだけの変更では変わらないことを確認する。実Pod rolloutは行わない。
package checksum

import (
	"bytes"
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

func deploymentRejected(t *testing.T, service string, overrides ...string) {
	t.Helper()
	helm := os.Getenv("HELM")
	if helm == "" {
		helm = "helm"
	}
	args := []string{"template", service, "../../charts/" + service, "-f", "../../environments/local/" + service + ".yaml"}
	for _, v := range overrides {
		args = append(args, "--set", v)
	}
	if data, err := exec.Command(helm, args...).CombinedOutput(); err == nil {
		t.Fatalf("forbidden override accepted: %v\n%s", overrides, data)
	}
}

func TestConfigChangesRollPodTemplate(t *testing.T) {
	cases := map[string][]string{
		"frontend": {"restartNonce=fixture", "probes.readiness.periodSeconds=7"},
		"backend":  {"config.logLevel=debug", "restartNonce=fixture", "probes.readiness.periodSeconds=7"},
	}
	baselines := map[string][]string{
		"frontend": {},
		"backend":  {},
	}
	for service, overrides := range cases {
		t.Run(service, func(t *testing.T) {
			oldChecksum, oldTemplate := deployment(t, service, baselines[service]...)
			for _, override := range overrides {
				t.Run(override, func(t *testing.T) {
					checksum, template := deployment(t, service, append(append([]string{}, baselines[service]...), override)...)
					if reflect.DeepEqual(template, oldTemplate) {
						t.Fatalf("%s did not change PodTemplate", override)
					}
					if override == "config.logLevel=debug" && checksum == oldChecksum {
						t.Fatalf("%s did not change config checksum", override)
					}
					if override != "config.logLevel=debug" && checksum != oldChecksum {
						t.Fatalf("%s unexpectedly changed config checksum", override)
					}
					t.Logf("%s checksum %s -> %s", override, oldChecksum, checksum)
				})
			}
			deploymentRejected(t, service, "service.port=18080")
			// Secret参照変更でrolloutが必要になる場合も、ConfigMap checksumへSecretを含めてはいけない。Chart入出力にSecret実値は存在しない。
			if service == "backend" {
				for _, override := range []string{"config.dbHost=fixture-db", "config.dbPort=5433", "config.dbName=fixture", "config.sslmode=require", "secrets.existingSecret=fixture-secret"} {
					deploymentRejected(t, service, override)
				}
			} else {
				for _, override := range []string{"config.apiBasePath=/fixture-api", "config.apiUpstream=http://fixture-backend:8080"} {
					deploymentRejected(t, service, override)
				}
			}
		})
	}
}
