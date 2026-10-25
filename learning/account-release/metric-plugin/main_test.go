// 計測 plugin の要求・応答と判定条件を、外部サービスを差し替えて確認します。
package main

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	argo "github.com/argoproj/argo-rollouts/pkg/apis/rollouts/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func fixture() (*Provider, *argo.AnalysisRun, argo.Metric) {
	now := time.Unix(1000, 0)
	p := &Provider{now: func() time.Time { return now }, scope: func(*argo.AnalysisRun, config) (snapshot, error) {
		return snapshot{Identity: "fixture", Roles: map[string]role{"stable": {Hash: "aaaaa", Image: "ghcr.io/syuhei0519/backend-app@sha256:" + strings.Repeat("a", 64), Count: 2},
			"candidate": {Hash: "bbbbb", Image: "ghcr.io/syuhei0519/backend-app@sha256:" + strings.Repeat("b", 64), Count: 1}}}, nil
	}, coverage: func(map[string]role, time.Time) error { return nil }, query: func(q string, at time.Time) (float64, error) {
		if at.Unix() != 1000 || strings.Contains(q, "{{") {
			return 0, errors.New("unbound-or-wrong-time")
		}
		if strings.Contains(q, " / ") {
			return 1, nil
		}
		return 100, nil
	}}
	run := &argo.AnalysisRun{ObjectMeta: metav1.ObjectMeta{Name: "account-release-test", Namespace: namespace, UID: "fixture-uid", CreationTimestamp: metav1.NewTime(now.Add(-301 * time.Second))}}
	metric := argo.Metric{Provider: argo.MetricProvider{Plugin: map[string]json.RawMessage{pluginName: json.RawMessage(`{"stableHash":"aaaaa","candidateHash":"bbbbb","stage":"10"}`)}}}
	return p, run, metric
}

func TestBinaryChecksumRequired(t *testing.T) {
	path := filepath.Join(t.TempDir(), "binary")
	body := []byte("synthetic binary")
	if err := os.WriteFile(path, body, 0600); err != nil {
		t.Fatal(err)
	}
	expected := fmt.Sprintf("%x", sha256.Sum256(body))
	if verifyBinary(path, expected) != nil {
		t.Fatal("matching checksum rejected")
	}
	for _, bad := range []string{"", strings.Repeat("0", 64), "invalid"} {
		if verifyBinary(path, bad) == nil {
			t.Fatal("bad checksum accepted")
		}
	}
}

func TestSharedPolicyCases(t *testing.T) {
	raw, err := os.ReadFile("../policy-cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name                            string
		Requests, Availability, Latency *float64
		Expected                        string
	}
	if err = json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			p, run, m := fixture()
			normal := p.query
			p.query = func(q string, at time.Time) (float64, error) {
				if !strings.Contains(q, `rollout_revision="bbbbb"`) {
					return normal(q, at)
				}
				v := tc.Requests
				if strings.Contains(q, " / ") {
					v = tc.Availability
					if strings.HasPrefix(q, "(sum(") {
						v = tc.Latency
					}
				}
				if v == nil {
					return 0, errors.New("missing")
				}
				return *v, nil
			}
			result := p.Run(run, m)
			var v verdict
			json.Unmarshal([]byte(result.Value), &v)
			if v.Result != tc.Expected {
				t.Fatal(v.Result, tc.Expected, result.Message)
			}
		})
	}
}

func TestVerdictBranches(t *testing.T) {
	for _, tc := range []struct {
		name  string
		mode  string
		phase argo.AnalysisPhase
	}{
		{"normal", "ok", argo.AnalysisPhaseSuccessful}, {"error", "error", argo.AnalysisPhaseFailed},
		{"latency", "latency", argo.AnalysisPhaseFailed}, {"low-traffic", "low", argo.AnalysisPhaseInconclusive},
		{"missing", "missing", argo.AnalysisPhaseInconclusive}, {"timeout", "timeout", argo.AnalysisPhaseInconclusive},
		{"NaN", "nan", argo.AnalysisPhaseInconclusive}, {"violation-with-other-role-missing", "mixed", argo.AnalysisPhaseFailed},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p, run, m := fixture()
			normal := p.query
			p.query = func(q string, at time.Time) (float64, error) {
				candidate := strings.Contains(q, `rollout_revision="bbbbb"`)
				ratio := strings.Contains(q, " / ")
				latency := strings.HasPrefix(q, "(sum(") && ratio
				switch tc.mode {
				case "timeout":
					return 0, errors.New("timeout")
				case "missing":
					return 0, errors.New("empty")
				case "nan":
					return math.NaN(), nil
				case "low":
					if !ratio {
						return 19, nil
					}
				case "error":
					if candidate && ratio && !latency {
						return .9, nil
					}
				case "latency":
					if candidate && latency {
						return .8, nil
					}
				case "mixed":
					if candidate {
						return 0, errors.New("missing")
					}
					if ratio && !latency {
						return .8, nil
					}
				}
				return normal(q, at)
			}
			result := p.Run(run, m)
			if result.Phase != tc.phase {
				t.Fatalf("phase=%s expected=%s reason=%s", result.Phase, tc.phase, result.Message)
			}
			var v verdict
			if json.Unmarshal([]byte(result.Value), &v) != nil || v.WindowEnd != 1000 || v.WindowStart != 700 || v.Schema != "account-verdict-v1" {
				t.Fatal("missing auditable verdict")
			}
		})
	}
}

func TestGuardedScopeAndNoSilentRetry(t *testing.T) {
	p, run, m := fixture()
	run.CreationTimestamp = metav1.NewTime(time.Unix(900, 0))
	if p.Run(run, m).Phase != argo.AnalysisPhaseInconclusive {
		t.Fatal("previous stage window accepted")
	}
	p, run, m = fixture()
	run.Namespace = "default"
	if p.Run(run, m).Phase != argo.AnalysisPhaseInconclusive {
		t.Fatal("foreign namespace accepted")
	}
	p, run, m = fixture()
	p.scope = func(*argo.AnalysisRun, config) (snapshot, error) { return snapshot{}, errors.New("auth denied") }
	if p.Run(run, m).Phase != argo.AnalysisPhaseInconclusive {
		t.Fatal("runtime unavailable accepted")
	}
	calls := 0
	p.query = func(string, time.Time) (float64, error) { calls++; return 1, nil }
	pending := argo.Measurement{Phase: argo.AnalysisPhaseRunning}
	if p.Resume(run, m, pending).Phase != argo.AnalysisPhaseInconclusive || p.Terminate(run, m, pending).Phase != argo.AnalysisPhaseInconclusive || calls != 0 {
		t.Fatal("resume/cancel silently retried or succeeded")
	}
}

func TestMeasurementIdentityAndCompletionTime(t *testing.T) {
	p, run, m := fixture()
	scope := p.scope
	calls := 0
	p.scope = func(a *argo.AnalysisRun, c config) (snapshot, error) {
		s, e := scope(a, c)
		calls++
		if calls == 2 {
			s.Identity = "changed"
		}
		return s, e
	}
	if result := p.Run(run, m); result.Phase != argo.AnalysisPhaseInconclusive || result.Message != "runtime-changed-during-measurement" {
		t.Fatal(result)
	}
	p, run, m = fixture()
	p.coverage = func(map[string]role, time.Time) error { return errors.New("old pod instance") }
	if result := p.Run(run, m); result.Phase != argo.AnalysisPhaseInconclusive {
		t.Fatal(result)
	}
	p, run, m = fixture()
	ticks := 0
	p.now = func() time.Time { ticks++; return time.Unix(1000+int64(ticks-1), 0) }
	result := p.Run(run, m)
	if result.Phase != argo.AnalysisPhaseSuccessful || !result.FinishedAt.After(result.StartedAt.Time) {
		t.Fatal("completion time missing", result)
	}
}

func TestCompletedHundredEvaluatesCandidateOnly(t *testing.T) {
	p, run, m := fixture()
	m.Provider.Plugin[pluginName] = json.RawMessage(`{"stableHash":"aaaaa","candidateHash":"bbbbb","stage":"100"}`)
	normal := p.query
	p.query = func(q string, at time.Time) (float64, error) {
		if strings.Contains(q, `rollout_revision="aaaaa"`) {
			t.Fatal("100 percent evaluated idle stable traffic")
		}
		return normal(q, at)
	}
	if p.Run(run, m).Phase != argo.AnalysisPhaseSuccessful {
		t.Fatal("candidate-only stage failed")
	}
}

func TestOtherRoleCoverageCannotHideValidViolation(t *testing.T) {
	p, run, m := fixture()
	normal := p.query
	p.coverage = func(roles map[string]role, _ time.Time) error {
		if _, exists := roles["stable"]; exists {
			return errors.New("stable target_info missing")
		}
		return nil
	}
	p.query = func(q string, at time.Time) (float64, error) {
		if strings.Contains(q, `rollout_revision="aaaaa"`) {
			t.Fatal("unverified stable SLI queried")
		}
		if strings.Contains(q, " / ") && !strings.HasPrefix(q, "(sum(") {
			return .9, nil
		}
		return normal(q, at)
	}
	result := p.Run(run, m)
	if result.Phase != argo.AnalysisPhaseFailed || !strings.Contains(result.Message, "candidate-availability-below-99pct") || !strings.Contains(result.Message, "stable-sdk-pod-identity-unverified") {
		t.Fatal(result)
	}
}
