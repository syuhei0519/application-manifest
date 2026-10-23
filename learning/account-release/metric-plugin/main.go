// Account-only controller metric provider. It does not mutate Kubernetes or Git.
package main

import (
	"bytes"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/argoproj/argo-rollouts/metricproviders/plugin/rpc"
	argo "github.com/argoproj/argo-rollouts/pkg/apis/rollouts/v1alpha1"
	types "github.com/argoproj/argo-rollouts/utils/plugin/types"
	"github.com/hashicorp/go-plugin"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const pluginName = "learning-local/account-policy"
const namespace = "learning-account-release"
const prometheus = "http://learning-prometheus.observability.svc.cluster.local:9090"
const window = 300

//go:embed queries.json
var queryJSON []byte
var hashPattern = regexp.MustCompile(`^[a-z0-9]{5,16}$`)
var imagePattern = regexp.MustCompile(`^ghcr\.io/syuhei0519/backend-app@sha256:[a-f0-9]{64}$`)

type Provider struct {
	now      func() time.Time
	scope    func(*argo.AnalysisRun, config) (snapshot, error)
	coverage func(map[string]role, time.Time) error
	query    func(string, time.Time) (float64, error)
}
type config struct {
	Stable    string `json:"stableHash"`
	Candidate string `json:"candidateHash"`
	Stage     string `json:"stage"`
}
type role struct {
	Hash  string        `json:"revision_hash"`
	Image string        `json:"image"`
	Count int           `json:"pod_count"`
	Pods  []podIdentity `json:"pods"`
}
type podIdentity struct {
	UID      string `json:"uid"`
	Owner    string `json:"replica_set_uid"`
	ImageID  string `json:"image_id"`
	Source   string `json:"source_commit"`
	Restarts int32  `json:"restarts"`
	Started  string `json:"started_at"`
}
type snapshot struct {
	Identity string
	Roles    map[string]role
}
type sample struct {
	role
	Requests     *float64 `json:"requests"`
	Availability *float64 `json:"availability"`
	Latency      *float64 `json:"latency_fraction"`
}
type verdict struct {
	Schema        string             `json:"schema"`
	Result        string             `json:"verdict"`
	Reasons       []string           `json:"reasons"`
	WindowStart   int64              `json:"window_start"`
	WindowEnd     int64              `json:"window_end"`
	WindowSeconds int                `json:"window_seconds"`
	Run           string             `json:"analysis_run"`
	RunUID        string             `json:"analysis_run_uid"`
	Roles         map[string]sample  `json:"roles"`
	Policy        map[string]float64 `json:"policy"`
}

func (p *Provider) InitPlugin() types.RpcError { return types.RpcError{} }
func (p *Provider) Type() string               { return pluginName }
func (p *Provider) GetMetadata(argo.Metric) map[string]string {
	return map[string]string{"policy": "account-v1", "source": "controller-metric-plugin", "windowSeconds": "300"}
}
func (p *Provider) GarbageCollect(*argo.AnalysisRun, argo.Metric, int) types.RpcError {
	return types.RpcError{}
}
func (p *Provider) Resume(_ *argo.AnalysisRun, _ argo.Metric, m argo.Measurement) argo.Measurement {
	// Run is synchronous and read-only. Never restart a query silently after RPC uncertainty.
	if !m.Phase.Completed() {
		m.Phase = argo.AnalysisPhaseInconclusive
		m.Message = "interrupted-measurement-requires-explicit-new-analysis"
	}
	return m
}
func (p *Provider) Terminate(_ *argo.AnalysisRun, _ argo.Metric, m argo.Measurement) argo.Measurement {
	if !m.Phase.Completed() {
		m.Phase = argo.AnalysisPhaseInconclusive
		m.Message = "measurement-cancelled"
	}
	return m
}

func (p *Provider) Run(run *argo.AnalysisRun, metric argo.Metric) argo.Measurement {
	now := p.now().UTC()
	stamp := metav1.NewTime(now)
	m := argo.Measurement{StartedAt: &stamp, FinishedAt: &stamp, Phase: argo.AnalysisPhaseInconclusive}
	v := verdict{Schema: "account-verdict-v1", Result: "inconclusive", Reasons: []string{},
		WindowStart: now.Unix() - window, WindowEnd: now.Unix(), WindowSeconds: window,
		Roles: map[string]sample{}, Policy: map[string]float64{"minimum_requests": 20, "availability_min": .99, "latency_fraction_min": .95, "latency_bucket_seconds": .25, "query_timeout_seconds": 5}}
	finish := func(reason string) argo.Measurement {
		finished := metav1.NewTime(p.now().UTC())
		m.FinishedAt = &finished
		if reason != "" {
			v.Reasons = append(v.Reasons, reason)
		}
		raw, _ := json.Marshal(v)
		m.Value = string(raw)
		m.Message = strings.Join(v.Reasons, ",")
		return m
	}
	if run == nil || run.Namespace != namespace || run.Name == "" || run.UID == "" {
		return finish("analysis-target-unverified")
	}
	v.Run = run.Name
	v.RunUID = string(run.UID)
	// Every 5-minute window starts after THIS stage's AnalysisRun, not a previous stage.
	if run.CreationTimestamp.IsZero() || now.Sub(run.CreationTimestamp.Time) < window*time.Second {
		return finish("stage-window-not-complete")
	}
	raw, ok := metric.Provider.Plugin[pluginName]
	if !ok || len(metric.Provider.Plugin) != 1 {
		return finish("plugin-config-invalid")
	}
	var c config
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if dec.Decode(&c) != nil || dec.Decode(new(any)) != io.EOF || !hashPattern.MatchString(c.Stable) || !hashPattern.MatchString(c.Candidate) || c.Stable == c.Candidate || (c.Stage != "10" && c.Stage != "50" && c.Stage != "100") {
		return finish("revision-scope-invalid")
	}
	before, err := p.scope(run, c)
	roles := before.Roles
	if err != nil || roles["candidate"].Count < 1 || (c.Stage != "100" && roles["stable"].Count < 1) {
		return finish("runtime-scope-unavailable")
	}
	var templates map[string]string
	if json.Unmarshal(queryJSON, &templates) != nil {
		return finish("policy-template-invalid")
	}
	failures, unknown := []string{}, []string{}
	for _, name := range []string{"stable", "candidate"} {
		identity, exists := roles[name]
		if !exists {
			continue
		}
		if name == "stable" && c.Stage == "100" {
			continue
		}
		expectedHash := c.Stable
		if name == "candidate" {
			expectedHash = c.Candidate
		}
		if identity.Hash != expectedHash {
			return finish("runtime-revision-mismatch")
		}
		if identity.Count < 1 || identity.Count > 10 || !imagePattern.MatchString(identity.Image) {
			return finish("runtime-role-invalid")
		}
		s := sample{role: identity}
		if p.coverage == nil || p.coverage(map[string]role{name: identity}, now) != nil {
			unknown = append(unknown, name+"-sdk-pod-identity-unverified")
			v.Roles[name] = s
			continue
		}
		vals := map[string]*float64{}
		prefix := name
		if name == "candidate" {
			prefix = "latest"
		}
		for _, measure := range []string{"requests", "availability", "latency"} {
			expression := templates[prefix+"-"+measure]
			expression = strings.NewReplacer("{{args.namespace}}", namespace, "{{args.service}}", "account",
				"{{args.stable-hash}}", c.Stable, "{{args.latest-hash}}", c.Candidate,
				"{{stable-count}}", strconv.Itoa(roles["stable"].Count), "{{latest-count}}", strconv.Itoa(roles["candidate"].Count)).Replace(expression)
			value, e := p.query(expression, now)
			if e != nil || math.IsNaN(value) || math.IsInf(value, 0) {
				unknown = append(unknown, name+"-"+measure+"-unavailable")
				continue
			}
			copy := value
			vals[measure] = &copy
		}
		s.Requests = vals["requests"]
		s.Availability = vals["availability"]
		s.Latency = vals["latency"]
		v.Roles[name] = s
		if s.Requests == nil || *s.Requests < 20 {
			unknown = append(unknown, name+"-insufficient-traffic")
			continue
		}
		for _, check := range []struct {
			value     *float64
			threshold float64
			reason    string
		}{{s.Availability, .99, "availability-below-99pct"}, {s.Latency, .95, "latency-250ms-below-95pct"}} {
			if check.value == nil {
				continue
			}
			if *check.value < 0 || *check.value > 1 {
				unknown = append(unknown, name+"-invalid-range")
			} else if *check.value < check.threshold {
				failures = append(failures, name+"-"+check.reason)
			}
		}
	}
	after, err := p.scope(run, c)
	if err != nil || !reflect.DeepEqual(before, after) || p.now().Sub(now) >= 90*time.Second {
		return finish("runtime-changed-during-measurement")
	}
	v.Reasons = append(failures, unknown...)
	if len(failures) > 0 {
		v.Result = "failed"
		m.Phase = argo.AnalysisPhaseFailed
	} else if len(unknown) == 0 {
		v.Result = "passed"
		m.Phase = argo.AnalysisPhaseSuccessful
		v.Reasons = []string{"all-observed-roles-within-policy"}
	}
	return finish("")
}

func readJSON(client *http.Client, endpoint, token string, value any) error {
	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	res, err := client.Do(req)
	if err != nil {
		return errors.New("read-unavailable")
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return errors.New("read-rejected")
	}
	return json.NewDecoder(io.LimitReader(res.Body, 4<<20)).Decode(value)
}

type promSample struct {
	Labels map[string]string
	Value  float64
}

func query(expression string, at time.Time) (float64, error) {
	rows, err := queryVector(expression, at)
	if err != nil {
		return 0, err
	}
	if len(rows) != 1 || len(rows[0].Labels) != 0 {
		return 0, errors.New("query-scope-invalid")
	}
	return rows[0].Value, nil
}
func queryVector(expression string, at time.Time) ([]promSample, error) {
	client := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect-denied") }}
	return readVector(client, prometheus, expression, at)
}
func readVector(client *http.Client, base, expression string, at time.Time) ([]promSample, error) {
	endpoint := base + "/api/v1/query?" + url.Values{"query": {expression}, "time": {strconv.FormatInt(at.Unix(), 10)}}.Encode()
	var body struct {
		Status          string
		Warnings, Infos []string
		Data            struct {
			ResultType string
			Result     []struct {
				Metric map[string]string
				Value  []json.RawMessage
			}
		}
	}
	if err := readJSON(client, endpoint, "", &body); err != nil {
		return nil, err
	}
	if body.Status != "success" || len(body.Warnings) > 0 || len(body.Infos) > 0 || body.Data.ResultType != "vector" {
		return nil, errors.New("query-incomplete")
	}
	rows := []promSample{}
	for _, item := range body.Data.Result {
		var eval float64
		var value string
		if len(item.Value) != 2 || json.Unmarshal(item.Value[0], &eval) != nil || eval != float64(at.Unix()) || json.Unmarshal(item.Value[1], &value) != nil {
			return nil, errors.New("query-time-invalid")
		}
		number, err := strconv.ParseFloat(value, 64)
		if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
			return nil, errors.New("query-nonfinite")
		}
		rows = append(rows, promSample{Labels: item.Metric, Value: number})
	}
	return rows, nil
}

// Scope is discovered from this controller's mounted in-cluster identity only.
// There is no external endpoint/token input, kubeconfig, or credential logging.
func runtimeScope(run *argo.AnalysisRun, c config) (snapshot, error) {
	token, err := os.ReadFile("/var/run/secrets/kubernetes.io/serviceaccount/token")
	if err != nil {
		return snapshot{}, err
	}
	pem, err := os.ReadFile("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
	if err != nil {
		return snapshot{}, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pem) {
		return snapshot{}, errors.New("ca-invalid")
	}
	client := &http.Client{Timeout: 5 * time.Second, Transport: &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: pool}}, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect-denied") }}
	return readScope(client, "https://kubernetes.default.svc", string(token), run, c)
}

func main() {
	// Rollouts v1.10.1 does not verify sha256 for file:// plugins. Enforce
	// the reviewed deployment checksum ourselves before opening the RPC server.
	executable, err := os.Executable()
	if err != nil || verifyBinary(executable, os.Getenv("ACCOUNT_POLICY_SHA256")) != nil {
		os.Exit(1)
	}
	p := &Provider{now: time.Now, scope: runtimeScope, coverage: sdkCoverage, query: query}
	plugin.Serve(&plugin.ServeConfig{HandshakeConfig: plugin.HandshakeConfig{ProtocolVersion: 1, MagicCookieKey: "ARGO_ROLLOUTS_RPC_PLUGIN", MagicCookieValue: "metricprovider"}, Plugins: map[string]plugin.Plugin{"RpcMetricProviderPlugin": &rpc.RpcMetricProviderPlugin{Impl: p}}})
}

func verifyBinary(path, expected string) error {
	if !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(expected) {
		return errors.New("checksum-required")
	}
	file, err := os.Open(path)
	if err != nil {
		return errors.New("binary-unavailable")
	}
	defer file.Close()
	hash := sha256.New()
	if _, err = io.Copy(hash, file); err != nil {
		return errors.New("binary-unreadable")
	}
	if hex.EncodeToString(hash.Sum(nil)) != expected {
		return errors.New("binary-checksum-mismatch")
	}
	return nil
}
