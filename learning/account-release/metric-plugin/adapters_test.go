package main

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	netrpc "net/rpc"
	"strings"
	"testing"
	"time"

	rpcplugin "github.com/argoproj/argo-rollouts/metricproviders/plugin/rpc"
	argo "github.com/argoproj/argo-rollouts/pkg/apis/rollouts/v1alpha1"
	argoconfig "github.com/argoproj/argo-rollouts/utils/config"
	"github.com/argoproj/argo-rollouts/utils/defaults"
	types "github.com/argoproj/argo-rollouts/utils/plugin/types"
	core "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

func TestOfficialRegistrationAndRPC(t *testing.T) {
	for _, name := range []string{pluginName, "learning.local/account-policy"} {
		client := fake.NewSimpleClientset(&core.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "test", Namespace: defaults.Namespace()}, Data: map[string]string{"metricProviderPlugins": "- name: " + name + "\n  location: file:///opt/account-policy\n  sha256: " + strings.Repeat("a", 64)}})
		cfg, err := argoconfig.InitializeConfig(client, "test")
		if name == pluginName {
			if err != nil || cfg.GetPlugin(name, types.PluginTypeMetricProvider) == nil {
				t.Fatal(err)
			}
		} else if err == nil {
			t.Fatal("invalid plugin accepted")
		}
		argoconfig.UnInitializeConfig()
	}
	p, run, m := fixture()
	extension := &rpcplugin.RpcMetricProviderPlugin{Impl: p}
	serverImpl, err := extension.Server(nil)
	if err != nil {
		t.Fatal(err)
	}
	server := netrpc.NewServer()
	if err = server.RegisterName("Plugin", serverImpl); err != nil {
		t.Fatal(err)
	}
	a, b := net.Pipe()
	defer a.Close()
	defer b.Close()
	go server.ServeConn(a)
	connection := netrpc.NewClient(b)
	defer connection.Close()
	implementation, err := extension.Client(nil, connection)
	if err != nil {
		t.Fatal(err)
	}
	client := implementation.(rpcplugin.MetricProviderPlugin)
	if client.InitPlugin().ErrorString != "" || client.Type() != pluginName {
		t.Fatal("RPC init/type failed")
	}
	if result := client.Run(run, m); result.Phase != argo.AnalysisPhaseSuccessful {
		t.Fatal(result)
	}
	connection.Close()
	if result := client.Run(run, m); result.Phase != argo.AnalysisPhaseError {
		t.Fatal("RPC loss must remain platform Error")
	}
}

func TestPrometheusHTTPParserAndTimeout(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		valid      bool
	}{
		{"valid", `{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1000,"1"]}]}}`, true},
		{"nan", `{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1000,"NaN"]}]}}`, false},
		{"wrong-time", `{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[999,"1"]}]}}`, false},
		{"warning", `{"status":"success","warnings":["partial"],"data":{"resultType":"vector","result":[]}}`, false},
		{"malformed", `{`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Query().Get("time") != "1000" {
					t.Error("missing exact time")
				}
				fmt.Fprint(w, tc.body)
			}))
			defer srv.Close()
			_, err := readVector(srv.Client(), srv.URL, "vector(1)", time.Unix(1000, 0))
			if (err == nil) != tc.valid {
				t.Fatal(err)
			}
		})
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() }))
	defer srv.Close()
	client := srv.Client()
	client.Timeout = 5 * time.Second
	started := time.Now()
	_, err := readVector(client, srv.URL, "vector(1)", time.Unix(1000, 0))
	if err == nil || time.Since(started) < 4900*time.Millisecond || time.Since(started) > 6*time.Second {
		t.Fatal("5-second timeout not enforced", err, time.Since(started))
	}
}

func runtimeFixture() (*argo.AnalysisRun, config, map[string]any) {
	_, run, _ := fixture()
	run.OwnerReferences = []metav1.OwnerReference{{UID: "rollout-uid", Name: "account-release", Kind: "Rollout"}}
	metadata := func(uid string) map[string]any {
		return map[string]any{"name": "account-release", "namespace": namespace, "uid": uid, "generation": 3}
	}
	ro := map[string]any{}
	json.Unmarshal([]byte(`{"spec":{"strategy":{"canary":{"steps":[{"setWeight":10},{"analysis":{"templates":[{"templateName":"policy"}]}}]}}},"status":{"observedGeneration":"3","currentPodHash":"bbbbb","stableRS":"aaaaa","currentStepIndex":1}}`), &ro)
	ro["metadata"] = metadata("rollout-uid")
	ro["status"].(map[string]any)["canary"] = map[string]any{"currentStepAnalysisRunStatus": map[string]any{"name": run.Name}}
	objects := map[string]any{"rollouts/account-release": ro, "analysisruns/" + run.Name: run}
	objects["virtualservices/account-release"] = map[string]any{"metadata": metadata("vs-uid"), "spec": map[string]any{"hosts": []string{serviceHost}, "gateways": []string{"mesh"}, "http": []any{map[string]any{"name": "primary", "route": []any{map[string]any{"weight": 90, "destination": map[string]any{"host": serviceHost, "subset": "stable", "port": map[string]int{"number": 8080}}}, map[string]any{"weight": 10, "destination": map[string]any{"host": serviceHost, "subset": "canary", "port": map[string]int{"number": 8080}}}}}}}}
	objects["destinationrules/account-release"] = map[string]any{"metadata": metadata("dr-uid"), "spec": map[string]any{"host": serviceHost, "subsets": []any{map[string]any{"name": "stable", "labels": map[string]string{"app": "account-release", "rollouts-pod-template-hash": "aaaaa"}}, map[string]any{"name": "canary", "labels": map[string]string{"app": "account-release", "rollouts-pod-template-hash": "bbbbb"}}}}}
	pods, sets := []any{}, []any{}
	for _, letter := range []string{"a", "b"} {
		hash := strings.Repeat(letter, 5)
		image := "ghcr.io/syuhei0519/backend-app@sha256:" + strings.Repeat(letter, 64)
		rsmeta := metadata("rs-" + letter)
		rsmeta["labels"] = map[string]string{"rollouts-pod-template-hash": hash}
		rsmeta["ownerReferences"] = run.OwnerReferences
		sets = append(sets, map[string]any{"metadata": rsmeta, "spec": map[string]any{"replicas": 1, "template": desiredTemplate(image, strings.Repeat(letter, 40))}, "status": map[string]int{"observedGeneration": 3, "replicas": 1, "readyReplicas": 1}})
		if letter == "b" {
			ro["spec"].(map[string]any)["template"] = desiredTemplate(image, strings.Repeat(letter, 40))
		}
		pm := metadata("pod-" + letter)
		pm["labels"] = rsmeta["labels"]
		pm["ownerReferences"] = []any{map[string]string{"uid": "rs-" + letter, "kind": "ReplicaSet"}}
		pm["annotations"] = map[string]string{"learning.local/source-commit": strings.Repeat(letter, 40), "learning.local/image": image}
		pods = append(pods, map[string]any{"metadata": pm, "spec": map[string]any{"containers": []any{map[string]string{"name": "api", "image": image}}}, "status": map[string]any{"phase": "Running", "conditions": []any{map[string]string{"type": "Ready", "status": "True"}}, "containerStatuses": []any{map[string]any{"name": "api", "ready": true, "imageID": image, "state": map[string]any{"running": map[string]string{"startedAt": "1970-01-01T00:01:00Z"}}}}}})
	}
	objects["pods"] = map[string]any{"items": pods}
	objects["replicasets"] = map[string]any{"items": sets}
	return run, config{Stable: "aaaaa", Candidate: "bbbbb", Stage: "10"}, objects
}
func desiredTemplate(image, source string) map[string]any {
	return map[string]any{"metadata": map[string]any{"annotations": map[string]string{"learning.local/image": image, "learning.local/source-commit": source}}, "spec": map[string]any{"containers": []any{map[string]string{"name": "api", "image": image}}}}
}

func TestRuntimeHTTPIdentityGuards(t *testing.T) {
	for _, mode := range []string{"ok", "generation", "run", "step", "subset", "image", "pod-image-replaced", "rs-and-pod-replaced", "owner", "pod-unready", "auth"} {
		t.Run(mode, func(t *testing.T) {
			run, c, objects := runtimeFixture()
			ro := objects["rollouts/account-release"].(map[string]any)["status"].(map[string]any)
			switch mode {
			case "generation":
				ro["observedGeneration"] = "2"
			case "run":
				ro["canary"] = map[string]any{"currentStepAnalysisRunStatus": map[string]string{"name": "old-run"}}
			case "step":
				ro["currentStepIndex"] = 0
			case "subset":
				objects["destinationrules/account-release"].(map[string]any)["spec"].(map[string]any)["subsets"].([]any)[0].(map[string]any)["labels"].(map[string]string)["rollouts-pod-template-hash"] = "ccccc"
			case "image":
				objects["pods"].(map[string]any)["items"].([]any)[0].(map[string]any)["status"].(map[string]any)["containerStatuses"].([]any)[0].(map[string]any)["imageID"] = "wrong"
			case "pod-image-replaced", "rs-and-pod-replaced":
				image := "ghcr.io/syuhei0519/backend-app@sha256:" + strings.Repeat("c", 64)
				source := strings.Repeat("c", 40)
				pod := objects["pods"].(map[string]any)["items"].([]any)[1].(map[string]any)
				pod["metadata"].(map[string]any)["annotations"] = map[string]string{"learning.local/image": image, "learning.local/source-commit": source}
				pod["spec"] = desiredTemplate(image, source)["spec"]
				pod["status"].(map[string]any)["containerStatuses"].([]any)[0].(map[string]any)["imageID"] = image
				if mode == "rs-and-pod-replaced" {
					objects["replicasets"].(map[string]any)["items"].([]any)[1].(map[string]any)["spec"].(map[string]any)["template"] = desiredTemplate(image, source)
				}
			case "owner":
				run.OwnerReferences = nil
			case "pod-unready":
				objects["pods"].(map[string]any)["items"].([]any)[0].(map[string]any)["status"].(map[string]any)["phase"] = "Pending"
			}
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if mode == "auth" {
					w.WriteHeader(403)
					return
				}
				if r.Header.Get("Authorization") != "Bearer synthetic-fixture-only" {
					t.Error("identity not sent")
				}
				key := strings.Split(r.URL.Path, "/namespaces/"+namespace+"/")
				if len(key) != 2 || objects[key[1]] == nil {
					w.WriteHeader(404)
					return
				}
				json.NewEncoder(w).Encode(objects[key[1]])
			}))
			defer srv.Close()
			s, err := readScope(srv.Client(), srv.URL, "synthetic-fixture-only", run, c)
			if mode == "ok" {
				if err != nil || s.Roles["candidate"].Pods[0].UID != "pod-b" {
					t.Fatal(s, err)
				}
			} else if err == nil {
				t.Fatal("unsafe scope accepted", mode)
			}
		})
	}
}

func TestSDKInstancesMustBindCurrentPods(t *testing.T) {
	roles := map[string]role{"candidate": {Hash: "bbbbb", Count: 1, Pods: []podIdentity{{UID: "current-pod", Source: strings.Repeat("b", 40)}}}}
	for _, mode := range []string{"ok", "replaced-pod", "wrong-source", "old-heartbeat", "stale", "duplicate"} {
		t.Run(mode, func(t *testing.T) {
			err := verifyCoverage(roles, time.Unix(1000, 0), func(q string, _ time.Time) ([]promSample, error) {
				labels := map[string]string{"instance": "current-instance", "k8s_pod_uid": "current-pod", "service_version": strings.Repeat("b", 40), "rollout_revision": "bbbbb"}
				value := float64(1)
				if strings.HasPrefix(q, "timestamp(") {
					value = 990
				}
				switch mode {
				case "replaced-pod":
					labels["k8s_pod_uid"] = "old-pod"
				case "wrong-source":
					labels["service_version"] = strings.Repeat("a", 40)
				case "old-heartbeat":
					if strings.Contains(q, "goroutines") {
						labels["instance"] = "old-instance"
					}
				case "stale":
					if strings.HasPrefix(q, "timestamp(") {
						value = 900
					}
				}
				rows := []promSample{{Labels: labels, Value: value}}
				if mode == "duplicate" {
					rows = append(rows, rows[0])
				}
				return rows, nil
			})
			if (err == nil) != (mode == "ok") {
				t.Fatal(mode, err)
			}
		})
	}
}
