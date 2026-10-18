package analysis_test

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os/exec"
	"strings"
	"testing"
	"time"

	provider "github.com/argoproj/argo-rollouts/metricproviders/prometheus"
	"github.com/argoproj/argo-rollouts/pkg/apis/rollouts/v1alpha1"
	log "github.com/sirupsen/logrus"
	"k8s.io/apimachinery/pkg/util/yaml"
)

// The actual pinned provider and expression evaluator, including its HTTP parser.
// This does not substitute for generated AnalysisRun/controller acceptance.
func TestPrometheusGate(t *testing.T) {
	rendered, err := exec.Command("helm", "template", "learning-account", "../chart", "--namespace", "learning-fixture",
		"--set", "owner=synthetic-fixture", "--set", "imageConfigID=sha256:"+strings.Repeat("a", 64),
		"--set", "analysis.enabled=true", "--set", "analysis.mode=metrics", "--set", "telemetry.enabled=true").Output()
	if err != nil {
		t.Fatal(err)
	}
	decoder := yaml.NewYAMLOrJSONDecoder(strings.NewReader(string(rendered)), 4096)
	var template v1alpha1.AnalysisTemplate
	for {
		var raw json.RawMessage
		if err := decoder.Decode(&raw); err == io.EOF {
			break
		} else if err != nil {
			t.Fatal(err)
		}
		var obj v1alpha1.AnalysisTemplate
		if err := json.Unmarshal(raw, &obj); err != nil {
			t.Fatal(err)
		}
		if obj.Kind == "AnalysisTemplate" {
			template = obj
		}
	}
	if len(template.Spec.Metrics) != 6 {
		t.Fatalf("metrics=%d", len(template.Spec.Metrics))
	}
	for _, metric := range template.Spec.Metrics {
		t.Run(metric.Name, func(t *testing.T) {
			if metric.Provider.Prometheus.Timeout == nil || *metric.Provider.Prometheus.Timeout != 5 {
				t.Fatal("timeout must be 5s")
			}
			good := "1"
			bad := "0.90"
			over := "1.01"
			overExpected := v1alpha1.AnalysisPhaseFailed
			if strings.HasSuffix(metric.Name, "requests") {
				good = "30"
				bad = "19"
				over = "30"
				overExpected = v1alpha1.AnalysisPhaseSuccessful
			}
			for _, tc := range []struct {
				name, value string
				expected    v1alpha1.AnalysisPhase
			}{
				{"good", good, v1alpha1.AnalysisPhaseSuccessful},
				{"threshold", bad, v1alpha1.AnalysisPhaseFailed},
				{"nan", "NaN", v1alpha1.AnalysisPhaseFailed},
				{"infinity", "+Inf", v1alpha1.AnalysisPhaseFailed},
				{"negative", "-1", v1alpha1.AnalysisPhaseFailed},
				{"over-range", over, overExpected},
				{"empty", "", v1alpha1.AnalysisPhaseFailed},
				{"multiple", "multiple", v1alpha1.AnalysisPhaseFailed},
				{"non-json", "non-json", v1alpha1.AnalysisPhaseError},
				{"query-error", "query-error", v1alpha1.AnalysisPhaseError},
				{"provider-error", "provider-error", v1alpha1.AnalysisPhaseError},
			} {
				t.Run(tc.name, func(t *testing.T) {
					server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						switch tc.value {
						case "non-json":
							_, _ = w.Write([]byte("not JSON"))
							return
						case "provider-error":
							w.WriteHeader(503)
							_, _ = w.Write([]byte(`{"status":"error","errorType":"unavailable","error":"fixture"}`))
							return
						case "query-error":
							_, _ = w.Write([]byte(`{"status":"error","errorType":"bad_data","error":"fixture"}`))
							return
						}
						values := []any{}
						if tc.value != "" {
							value := tc.value
							if value == "multiple" {
								value = good
							}
							values = append(values, map[string]any{"metric": map[string]string{}, "value": []any{float64(1), value}})
							if tc.value == "multiple" {
								values = append(values, values[0])
							}
						}
						_ = json.NewEncoder(w).Encode(map[string]any{"status": "success", "data": map[string]any{"resultType": "vector", "result": values}})
					}))
					defer server.Close()
					current := metric.DeepCopy()
					current.Provider.Prometheus.Address = server.URL
					api, err := provider.NewPrometheusAPI(*current)
					if err != nil {
						t.Fatal(err)
					}
					p, err := provider.NewPrometheusProvider(api, *log.NewEntry(log.New()), *current)
					if err != nil {
						t.Fatal(err)
					}
					result := p.Run(&v1alpha1.AnalysisRun{}, *current)
					if result.Phase != tc.expected {
						t.Fatalf("phase=%s expected=%s message=%s", result.Phase, tc.expected, result.Message)
					}
				})
			}
			if !strings.HasSuffix(metric.Name, "requests") {
				current := metric.DeepCopy()
				current.Provider.Prometheus.Address = "http://127.0.0.1:1"
				api, _ := provider.NewPrometheusAPI(*current)
				p, _ := provider.NewPrometheusProvider(api, *log.NewEntry(log.New()), *current)
				if p.Run(&v1alpha1.AnalysisRun{}, *current).Phase != v1alpha1.AnalysisPhaseError {
					t.Fatal("connection error accepted")
				}
			}
		})
	}
	t.Run("timeout", func(t *testing.T) {
		// The production 5s timeout is exercised, rather than replacing it for tests.
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			_, _ = io.Copy(io.Discard, r.Body)
			select {
			case <-r.Context().Done():
			case <-time.After(6 * time.Second):
			}
		}))
		defer server.Close()
		metric := template.Spec.Metrics[0].DeepCopy()
		metric.Provider.Prometheus.Address = server.URL
		api, _ := provider.NewPrometheusAPI(*metric)
		p, _ := provider.NewPrometheusProvider(api, *log.NewEntry(log.New()), *metric)
		start := time.Now()
		result := p.Run(&v1alpha1.AnalysisRun{}, *metric)
		if result.Phase != v1alpha1.AnalysisPhaseError || time.Since(start) > 7*time.Second {
			t.Fatalf("timeout phase=%s elapsed=%s", result.Phase, time.Since(start))
		}
	})
}
