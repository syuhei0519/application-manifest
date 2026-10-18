package analysis_test

import (
	"bufio"
	"encoding/json"
	"io"
	"net/url"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/argoproj/argo-rollouts/metricproviders/webmetric"
	"github.com/argoproj/argo-rollouts/pkg/apis/rollouts/v1alpha1"
	log "github.com/sirupsen/logrus"
	"k8s.io/apimachinery/pkg/util/yaml"
)

// Exercise the real pinned web provider, JSONPath and expression evaluator against
// the Python fixture. This is not a Rollout/controller or cluster acceptance test.
func TestFixedProvider(t *testing.T) {
	chart, err := filepath.Abs("../chart")
	if err != nil {
		t.Fatal(err)
	}
	rendered, err := exec.Command("helm", "template", "learning-account", chart, "--namespace", "learning-fixture",
		"--kube-version", "1.36.4", "--set", "owner=synthetic-fixture", "--set", "imageConfigID=sha256:"+strings.Repeat("a", 64),
		"--set", "analysis.enabled=true", "--set", "revision=candidate").Output()
	if err != nil {
		t.Fatal(err)
	}
	decoder := yaml.NewYAMLOrJSONDecoder(strings.NewReader(string(rendered)), 4096)
	var template v1alpha1.AnalysisTemplate
	for {
		var obj json.RawMessage
		if err := decoder.Decode(&obj); err == io.EOF {
			break
		} else if err != nil {
			t.Fatal(err)
		}
		var candidate v1alpha1.AnalysisTemplate
		if err := json.Unmarshal(obj, &candidate); err != nil {
			t.Fatal(err)
		}
		if candidate.Kind == "AnalysisTemplate" {
			template = candidate
		}
	}
	if len(template.Spec.Metrics) != 1 {
		t.Fatal("expected the rendered metric")
	}
	// Bind only loopback and an OS-selected port in Cloud. The deployed fixture uses ClusterIP.
	script := `import sys; sys.path.insert(0, sys.argv[1]); from provider import Handler, ThreadingHTTPServer; s=ThreadingHTTPServer(('127.0.0.1',0),Handler); print(s.server_port,flush=True); s.serve_forever()`
	cmd := exec.Command("python3", "-B", "-c", script, filepath.Join(chart, "files"))
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait() })
	scanner := bufio.NewScanner(stdout)
	if !scanner.Scan() {
		t.Fatal("fixture did not start")
	}
	endpoint := "http://127.0.0.1:" + scanner.Text() + "/measure?"
	args := map[string]string{"service": "account", "namespace": "learning-fixture", "latest-hash": "latest-fixture", "stable-hash": "stable-fixture"}
	for _, tc := range []struct {
		name     string
		expected v1alpha1.AnalysisPhase
	}{
		{"success", v1alpha1.AnalysisPhaseSuccessful}, {"failure", v1alpha1.AnalysisPhaseFailed},
		{"inconclusive", v1alpha1.AnalysisPhaseInconclusive}, {"missing", v1alpha1.AnalysisPhaseError},
		{"timeout", v1alpha1.AnalysisPhaseError}, {"mismatched-hash", v1alpha1.AnalysisPhaseFailed},
	} {
		t.Run(tc.name, func(t *testing.T) {
			metric := template.Spec.Metrics[0].DeepCopy()
			for key, value := range args {
				metric.SuccessCondition = strings.ReplaceAll(metric.SuccessCondition, "{{args."+key+"}}", value)
				metric.FailureCondition = strings.ReplaceAll(metric.FailureCondition, "{{args."+key+"}}", value)
			}
			query := url.Values{}
			for key, value := range args {
				query.Set(key, value)
			}
			query.Set("case", tc.name)
			if tc.name == "mismatched-hash" {
				query.Set("case", "success")
				query.Set("latest-hash", "wrong-revision")
			}
			metric.Provider.Web.URL = endpoint + query.Encode()
			client, err := webmetric.NewWebMetricHttpClient(*metric)
			if err != nil {
				t.Fatal(err)
			}
			parser, err := webmetric.NewWebMetricJsonParser(*metric)
			if err != nil {
				t.Fatal(err)
			}
			provider := webmetric.NewWebMetricProvider(*log.NewEntry(log.New()), client, parser)
			measurement := provider.Run(&v1alpha1.AnalysisRun{}, *metric)
			if measurement.Phase != tc.expected {
				t.Fatalf("phase=%s expected=%s message=%s", measurement.Phase, tc.expected, measurement.Message)
			}
		})
	}
}
