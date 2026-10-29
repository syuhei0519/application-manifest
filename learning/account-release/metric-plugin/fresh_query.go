package main

import (
	"encoding/json"
	"errors"
	"fmt"
	argo "github.com/argoproj/argo-rollouts/pkg/apis/rollouts/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"math"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"time"
)

var freshUID = regexp.MustCompile(`^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`)

// freshScope constructs an exact UID filter from controller-verified Pods. The
// target_info join preserves SDK identity despite Prometheus honor_labels=false.
func freshScope(pods []podIdentity) (string, error) {
	if len(pods) == 0 || len(pods) > 10 {
		return "", errors.New("pod-count-invalid")
	}
	uids := make([]string, 0, len(pods))
	seen := map[string]bool{}
	for _, pod := range pods {
		if !freshUID.MatchString(pod.UID) || seen[pod.UID] {
			return "", errors.New("pod-uid-invalid")
		}
		seen[pod.UID] = true
		uids = append(uids, pod.UID)
	}
	sort.Strings(uids)
	return fmt.Sprintf(`target_info{exported_job="account-backend",exported_instance!="",k8s_namespace_name="fresh-account",k8s_pod_uid=~"%s"}`, strings.Join(uids, "|")), nil
}

func freshMetricQuery(expression, target string) string {
	return expression + ` * on(exported_job,exported_instance) group_left(k8s_pod_uid) ` + target
}

// freshVerdict distinguishes an absent measurement from observed low traffic.
// The caller must first verify UID coverage and a complete stage-local window.
func freshVerdict(requests, good, fast *float64) (string, string) {
	if requests == nil || good == nil || fast == nil {
		return "inconclusive", "metrics-missing"
	}
	if math.IsNaN(*requests) || math.IsInf(*requests, 0) || math.IsNaN(*good) || math.IsInf(*good, 0) || math.IsNaN(*fast) || math.IsInf(*fast, 0) || *requests < 0 || *good < 0 || *fast < 0 || *good > *requests || *fast > *requests {
		return "inconclusive", "metrics-invalid"
	}
	if *requests < 20 {
		return "inconclusive", "insufficient-traffic"
	}
	if *good / *requests < .99 {
		return "failed", "availability-below-99pct"
	}
	if *fast / *requests < .95 {
		return "failed", "latency-250ms-below-95pct"
	}
	return "passed", "candidate-within-policy"
}

// runFresh measures only the controller-verified candidate Pods. Every Pod must
// contribute recent counters and histogram samples before any verdict is made.
func (p *Provider) runFresh(run *argo.AnalysisRun, c config, now time.Time, m argo.Measurement, v verdict, finish func(string) argo.Measurement) argo.Measurement {
	before, err := p.scope(run, c)
	candidate := before.Roles["candidate"]
	if err != nil || candidate.Hash != c.Candidate || candidate.Count != len(candidate.Pods) || candidate.Count < 1 {
		return finish("candidate-scope-unavailable")
	}
	total, good, fast := 0.0, 0.0, 0.0
	for _, pod := range candidate.Pods {
		started, err := time.Parse(time.RFC3339Nano, pod.Started)
		if err != nil || started.After(run.CreationTimestamp.Time) {
			return finish("candidate-window-incomplete")
		}
		target, err := freshScope([]podIdentity{pod})
		if err != nil {
			return finish("candidate-uid-invalid")
		}
		n, e := p.query(`count(`+target+`)`, now)
		if e != nil || n != 1 {
			return finish("candidate-target-coverage")
		}
		allTargets := `target_info{exported_job="account-backend",exported_instance!=""}`
		collision := `max(count by(exported_job,exported_instance)(` + allTargets + `) * on(exported_job,exported_instance) group_right() ` + target + `)`
		unique, e := p.query(collision, now)
		if e != nil || unique != 1 {
			return finish("candidate-instance-ambiguous")
		}
		age, e := p.query(`max(time()-timestamp(`+target+`))`, now)
		if e != nil || age < 0 || age >= 90 || math.IsNaN(age) {
			return finish("candidate-target-stale")
		}
		join := func(expr string) string {
			return freshMetricQuery(expr, target)
		}
		metric := `pe_http_server_requests_total{exported_job="account-backend"}`
		count := `pe_http_server_request_duration_seconds_count{exported_job="account-backend"}`
		bucket := `pe_http_server_request_duration_seconds_bucket{exported_job="account-backend",le="0.25"}`
		// Pair each request series with its duration count and both required
		// histogram buckets; aggregate equality alone can hide a missing series.
		infinity := `pe_http_server_request_duration_seconds_bucket{exported_job="account-backend",le="+Inf"}`
		dimensions := `on(exported_job,exported_instance,method,route,status_class)`
		for _, pair := range [][2]string{{metric, count}, {count, bucket}, {count, infinity}} {
			mismatch := `(` + pair[0] + ` unless ` + dimensions + ` ` + pair[1] + `) or (` + pair[1] + ` unless ` + dimensions + ` ` + pair[0] + `)`
			q := `count(` + join(`(`+mismatch+`)`) + `) or vector(0)`
			value, e := p.query(q, now)
			if e != nil || value != 0 {
				return finish("candidate-series-incomplete")
			}
		}
		// Values must agree per dimension; equal sums alone can hide loss.
		for _, pair := range [][2]string{{metric, count}, {count, infinity}} {
			mismatch := `abs(` + pair[0] + ` - ` + dimensions + ` ` + pair[1] + `) > 0.001`
			value, e := p.query(`count(`+join(`(`+mismatch+`)`)+`) or vector(0)`, now)
			if e != nil || value != 0 {
				return finish("candidate-series-inconsistent")
			}
		}

		for _, series := range []string{metric, count, bucket, infinity} {
			// Every existing series must have multiple observations and a recent sample.
			for _, expr := range []string{`min(` + join(`count_over_time(`+series+`[5m])`) + `)`, `max(` + join(`time()-timestamp(`+series+`)`) + `)`} {
				x, e := p.query(expr, now)
				if e != nil || math.IsNaN(x) || math.IsInf(x, 0) || (strings.HasPrefix(expr, "min(") && x < 2) || (strings.HasPrefix(expr, "max(") && (x < 0 || x >= 90)) {
					return finish("candidate-metrics-missing")
				}
			}
		}
		request, e := p.query(`sum(`+join(`increase(`+metric+`[5m])`)+`)`, now)
		if e != nil {
			return finish("candidate-metrics-missing")
		}
		duration, e := p.query(`sum(`+join(`increase(`+count+`[5m])`)+`)`, now)
		if e != nil || math.Abs(request-duration) > 0.01 {
			return finish("candidate-metrics-inconsistent")
		}
		successMetric := `pe_http_server_requests_total{exported_job="account-backend",status_class=~"2xx|3xx"}`
		success, e := p.query(`sum(`+join(`increase(`+successMetric+`[5m])`)+`) or vector(0)`, now)
		if e != nil {
			return finish("candidate-metrics-missing")
		}
		fastCount, e := p.query(`sum(`+join(`increase(`+bucket+`[5m])`)+`)`, now)
		if e != nil {
			return finish("candidate-metrics-missing")
		}
		total += request
		good += success
		fast += fastCount
	}
	after, err := p.scope(run, c)
	if err != nil || !reflect.DeepEqual(before, after) || p.now().Sub(now) >= 90*time.Second {
		return finish("runtime-changed-during-measurement")
	}
	result, reason := freshVerdict(&total, &good, &fast)
	s := sample{role: candidate, Requests: &total}
	if total > 0 {
		a, l := good/total, fast/total
		s.Availability = &a
		s.Latency = &l
	}
	v.Roles["candidate"] = s
	v.Result = result
	v.Reasons = append(v.Reasons, reason)
	switch result {
	case "passed":
		m.Phase = argo.AnalysisPhaseSuccessful
	case "failed":
		m.Phase = argo.AnalysisPhaseFailed
	}
	// finish closes over its original values, so serialize the completed verdict here.
	end := metav1.NewTime(p.now().UTC())
	m.FinishedAt = &end
	raw, _ := json.Marshal(v)
	m.Value = string(raw)
	m.Message = reason
	return m
}
