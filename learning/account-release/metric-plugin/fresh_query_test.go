package main

import (
	"errors"
	argo "github.com/argoproj/argo-rollouts/pkg/apis/rollouts/v1alpha1"
	"math"
	"strings"
	"testing"
	"time"
)

func TestFreshScopeRejectsInvalidOrRepeatedPods(t *testing.T) {
	id := "01234567-89ab-cdef-0123-456789abcdef"
	for _, pods := range [][]podIdentity{nil, {{UID: ".*"}}, {{UID: id}, {UID: id}}} {
		if _, err := freshScope(pods); err == nil {
			t.Fatalf("accepted %v", pods)
		}
	}
}
func TestFreshScopeSeparatesCandidateFromStable(t *testing.T) {
	candidate := "01234567-89ab-cdef-0123-456789abcdef"
	stable := "12345678-9abc-def0-1234-56789abcdef0"
	scope, err := freshScope([]podIdentity{{UID: candidate}})
	if err != nil || !strings.Contains(scope, candidate) || strings.Contains(scope, stable) {
		t.Fatal(scope, err)
	}
	query := freshMetricQuery(`pe_http_server_requests_total{exported_job="account-backend"}`, scope)
	for _, required := range []string{`exported_job="account-backend"`, `on(exported_job,exported_instance)`, `group_left(k8s_pod_uid)`, candidate} {
		if !strings.Contains(query, required) {
			t.Fatal(query)
		}
	}
}

func TestFreshVerdict(t *testing.T) {
	f := func(v float64) *float64 { return &v }
	cases := []struct {
		name            string
		req, good, fast *float64
		want, reason    string
	}{
		{"good", f(100), f(100), f(97), "passed", "candidate-within-policy"},
		{"errors", f(100), f(90), f(100), "failed", "availability-below-99pct"},
		{"latency", f(100), f(100), f(90), "failed", "latency-250ms-below-95pct"},
		{"low", f(19), f(19), f(19), "inconclusive", "insufficient-traffic"},
		{"zero", f(0), f(0), f(0), "inconclusive", "insufficient-traffic"},
		{"missing", nil, f(0), f(0), "inconclusive", "metrics-missing"},
		{"nan", f(math.NaN()), f(0), f(0), "inconclusive", "metrics-invalid"},
		{"overcount", f(20), f(21), f(20), "inconclusive", "metrics-invalid"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, reason := freshVerdict(tc.req, tc.good, tc.fast)
			if got != tc.want || reason != tc.reason {
				t.Fatalf("got %s %s", got, reason)
			}
		})
	}
}

func TestFreshPluginVerdicts(t *testing.T) {
	old, oldns := freshProfile, namespace
	freshProfile, namespace = true, "fresh-account"
	defer func() { freshProfile, namespace = old, oldns }()
	for _, tc := range []struct {
		name                   string
		traffic, success, fast float64
		missing                bool
		phase                  argo.AnalysisPhase
		reason                 string
	}{
		{"healthy", 100, 100, 98, false, argo.AnalysisPhaseSuccessful, "candidate-within-policy"},
		{"low", 19, 19, 19, false, argo.AnalysisPhaseInconclusive, "insufficient-traffic"},
		{"error", 100, 90, 100, false, argo.AnalysisPhaseFailed, "availability-below-99pct"},
		{"slow", 100, 100, 90, false, argo.AnalysisPhaseFailed, "latency-250ms-below-95pct"},
		{"missing", 100, 100, 100, true, argo.AnalysisPhaseInconclusive, "candidate-target-coverage"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p, run, metric := fixture()
			run.Namespace = namespace
			p.scope = func(*argo.AnalysisRun, config) (snapshot, error) {
				return snapshot{Identity: "fixed", Roles: map[string]role{"candidate": {Hash: "bbbbb", Count: 1, Pods: []podIdentity{{UID: "01234567-89ab-cdef-0123-456789abcdef", Started: time.Unix(1, 0).UTC().Format(time.RFC3339Nano)}}}}}, nil
			}
			p.query = func(q string, _ time.Time) (float64, error) {
				switch {
				case strings.HasPrefix(q, "count(target_info"):
					if tc.missing {
						return 0, errors.New("missing")
					}
					return 1, nil
				case strings.HasPrefix(q, "max(count by("):
					return 1, nil
				case strings.HasPrefix(q, "max(time()-timestamp(target_info"):
					return 0, nil
				case strings.HasPrefix(q, "count("):
					return 0, nil
				case strings.HasPrefix(q, "min("):
					return 2, nil
				case strings.HasPrefix(q, "max("):
					return 1, nil
				case strings.Contains(q, "status_class="):
					return tc.success, nil
				case strings.Contains(q, "_bucket{"):
					return tc.fast, nil
				default:
					return tc.traffic, nil
				}
			}
			got := p.Run(run, metric)
			if got.Phase != tc.phase || got.Message != tc.reason {
				t.Fatalf("phase=%v reason=%s", got.Phase, got.Message)
			}
		})
	}
}

func TestFreshPluginRejectsPartialCandidateCoverage(t *testing.T) {
	old, oldns := freshProfile, namespace
	freshProfile, namespace = true, "fresh-account"
	defer func() { freshProfile, namespace = old, oldns }()
	for _, missing := range []string{"target", "series", "bucket", "duplicate"} {
		t.Run(missing, func(t *testing.T) {
			p, run, metric := fixture()
			run.Namespace = namespace
			p.scope = func(*argo.AnalysisRun, config) (snapshot, error) {
				return snapshot{Identity: "fixed", Roles: map[string]role{"candidate": {Hash: "bbbbb", Count: 2, Pods: []podIdentity{{UID: "01234567-89ab-cdef-0123-456789abcdef", Started: "1970-01-01T00:00:01Z"}, {UID: "12345678-9abc-def0-1234-56789abcdef0", Started: "1970-01-01T00:00:01Z"}}}, "stable": {Hash: "aaaaa", Count: 1, Pods: []podIdentity{{UID: "23456789-abcd-ef01-2345-6789abcdef01"}}}}}, nil
			}
			p.query = func(q string, _ time.Time) (float64, error) {
				if strings.Contains(q, "23456789-abcd-ef01-2345-6789abcdef01") {
					t.Fatal("stable metrics used")
				}
				second := strings.Contains(q, "12345678-9abc-def0-1234-56789abcdef0")
				switch {
				case strings.HasPrefix(q, "count(target_info"):
					if second && missing == "target" {
						return 0, errors.New("missing")
					}
					return 1, nil
				case strings.HasPrefix(q, "max(count by("):
					if second && missing == "duplicate" {
						return 2, nil
					}
					return 1, nil
				case strings.HasPrefix(q, "max(time()-timestamp(target_info"):
					return 1, nil
				case strings.HasPrefix(q, "count("):
					if second && missing == "series" {
						return 1, nil
					}
					return 0, nil
				case strings.HasPrefix(q, "min("):
					if second && missing == "bucket" && strings.Contains(q, "_bucket{") {
						return 0, errors.New("missing")
					}
					return 3, nil
				case strings.HasPrefix(q, "max("):
					return 1, nil
				default:
					return 100, nil
				}
			}
			got := p.Run(run, metric)
			if got.Phase != argo.AnalysisPhaseInconclusive {
				t.Fatalf("partial %s passed: %s", missing, got.Message)
			}
		})
	}
}
