package checksum

import (
	"bytes"
	"gopkg.in/yaml.v3"
	"io"
	"os"
	"os/exec"
	"strings"
	"testing"
)

func orderRender(t *testing.T, chart, fixture string, overrides ...string) ([]map[string]any, error) {
	t.Helper()
	helm := os.Getenv("HELM")
	if helm == "" {
		helm = "helm"
	}
	args := []string{"template", chart, "../../charts/" + chart, "-f", "../../environments/local/" + chart + ".yaml"}
	if fixture != "" {
		args = append(args, "-f", "../../ci/fixtures/order/"+fixture)
	}
	for _, s := range overrides {
		args = append(args, "--set", s)
	}
	b, err := exec.Command(helm, args...).CombinedOutput()
	if err != nil {
		return nil, err
	}
	dec := yaml.NewDecoder(bytes.NewReader(b))
	var docs []map[string]any
	for {
		var d map[string]any
		err = dec.Decode(&d)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if d != nil {
			docs = append(docs, d)
		}
	}
	return docs, nil
}
func TestOrderDisabledAndSchemaBoundaries(t *testing.T) {
	for _, c := range []struct{ chart, fixture string }{{"order", "enabled.yaml"}, {"order-postgresql", "postgresql-enabled.yaml"}} {
		t.Run(c.chart, func(t *testing.T) {
			docs, err := orderRender(t, c.chart, "")
			if err != nil || len(docs) != 0 {
				t.Fatal("disabled resources", err)
			}
			docs, err = orderRender(t, c.chart, c.fixture, "enabled=false")
			if err != nil || len(docs) != 0 {
				t.Fatal("disabled fixture resources")
			}
			bad := []string{"enabled=oops", "replicaCount=2", "config.dbName=account", "secrets.existingSecret=account-db", "serviceAccount.automountServiceAccountToken=true", "podSecurityContext.runAsUser=0", "securityContext.readOnlyRootFilesystem=false", "securityContext.capabilities.add[0]=NET_ADMIN", "service.type=NodePort", "image.digest=sha256:bad", "extra=true"}
			if c.chart == "order" {
				bad = append(bad, "image.repository=ghcr.io/syuhei0519/backend-app", "image.tag=latest", "release.sourceCommit="+strings.Repeat("a", 40), "release.service=backend", "release.schemaVersion=github-v1", "release.runId=999", "release.recordSha256=bad", "migration.activeDeadlineSeconds=181", "migration.backoffLimit=2", "config.dbHost=postgresql", "secrets.usernameKey=admin-password")
			} else {
				bad = append(bad, "persistence.enabled=false", "persistence.size=1Gi", "persistence.storageClass=other", "image.tag=17.9-bookworm", "config.pgData=/tmp/data")
			}
			for _, v := range bad {
				t.Run(v, func(t *testing.T) {
					if _, err := orderRender(t, c.chart, c.fixture, v); err == nil {
						t.Fatal("unsafe values accepted")
					}
				})
			}
		})
	}
	if _, err := orderRender(t, "order", "", "enabled=true"); err == nil {
		t.Fatal("blank release enabled")
	}
}
func om(v any) map[string]any            { return v.(map[string]any) }
func oa(v any) []any                     { return v.([]any) }
func op(d map[string]any) map[string]any { return om(om(om(d["spec"])["template"])["spec"]) }
func TestOrderWorkloadContract(t *testing.T) {
	docs, err := orderRender(t, "order", "enabled.yaml")
	if err != nil {
		t.Fatal(err)
	}
	if len(docs) != 5 {
		t.Fatal("resource count")
	}
	by := map[string]map[string]any{}
	for _, d := range docs {
		by[d["kind"].(string)] = d
	}
	dep, job := by["Deployment"], by["Job"]
	for _, d := range []map[string]any{dep, job} {
		p := op(d)
		if p["serviceAccountName"] != "order" || p["automountServiceAccountToken"] != false {
			t.Fatal("service account")
		}
		c := om(oa(p["containers"])[0])
		env := map[string]map[string]any{}
		for _, e := range oa(c["env"]) {
			x := om(e)
			env[x["name"].(string)] = x
		}
		if env["PGDATABASE"]["value"] != "orders" || env["PGHOST"]["value"] != "order-postgresql" {
			t.Fatal("database isolation")
		}
		for _, k := range []string{"PGUSER", "PGPASSWORD"} {
			ref := om(om(env[k]["valueFrom"])["secretKeyRef"])
			if ref["name"] != "order-db" || !strings.HasPrefix(ref["key"].(string), "app-") {
				t.Fatal("credential boundary")
			}
		}
	}
	dc, jc := om(oa(op(dep)["containers"])[0]), om(oa(op(job)["containers"])[0])
	if dc["image"] != jc["image"] {
		t.Fatal("migration image mismatch")
	}
	js := om(job["spec"])
	if js["activeDeadlineSeconds"] != 180 || js["backoffLimit"] != 1 || op(job)["restartPolicy"] != "Never" || oa(jc["args"])[0] != "migrate" {
		t.Fatal("migration budget")
	}
	ja := om(om(job["metadata"])["annotations"])
	if ja["argocd.argoproj.io/hook"] != "Sync" || ja["argocd.argoproj.io/sync-wave"] != "-10" || ja["argocd.argoproj.io/hook-delete-policy"] != "BeforeHookCreation,HookSucceeded" {
		t.Fatal("hook order")
	}
	for _, k := range []string{"ConfigMap", "ServiceAccount"} {
		if om(om(by[k]["metadata"])["annotations"])["argocd.argoproj.io/sync-wave"] != "-20" {
			t.Fatal("prerequisite wave")
		}
	}
	for probe, want := range map[string]string{"startupProbe": "/livez", "livenessProbe": "/livez", "readinessProbe": "/readyz"} {
		if om(om(dc[probe])["httpGet"])["path"] != want {
			t.Fatal("probe")
		}
	}
}
func TestOrderDatabasePaths(t *testing.T) {
	docs, err := orderRender(t, "order-postgresql", "postgresql-enabled.yaml")
	if err != nil {
		t.Fatal(err)
	}
	if len(docs) != 6 {
		t.Fatal("resource count")
	}
	for _, d := range docs {
		switch d["kind"] {
		case "StatefulSet":
			c := om(oa(op(d)["containers"])[0])
			mounts := map[string]string{}
			for _, v := range oa(c["volumeMounts"]) {
				m := om(v)
				mounts[m["name"].(string)] = m["mountPath"].(string)
			}
			if mounts["data"] != "/var/lib/postgresql/data" || mounts["run"] != "/var/run/postgresql" || mounts["order-db"] != "/run/order-db" {
				t.Fatal("official image paths")
			}
		case "ConfigMap":
			script := om(d["data"])["10-order.sh"].(string)
			for _, s := range []string{`test "$app_user" = order_app`, "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT", "--set=db_name=\"orders\"", "CREATE DATABASE %I OWNER %I"} {
				if !strings.Contains(script, s) {
					t.Fatal("initialization", s)
				}
			}
		case "Secret", "Namespace":
			t.Fatal("forbidden resource")
		}
	}
}
