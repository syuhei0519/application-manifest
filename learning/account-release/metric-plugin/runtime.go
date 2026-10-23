package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	argo "github.com/argoproj/argo-rollouts/pkg/apis/rollouts/v1alpha1"
	apps "k8s.io/api/apps/v1"
	core "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

var sourcePattern = regexp.MustCompile(`^[a-f0-9]{40}$`)

const serviceHost = "account-release.learning-account-release.svc.cluster.local"

type virtualService struct {
	metav1.ObjectMeta `json:"metadata"`
	Spec              struct {
		Hosts    []string
		Gateways []string
		HTTP     []struct {
			Name  string
			Route []struct {
				Weight      int
				Destination struct {
					Host, Subset string
					Port         struct{ Number int }
				}
			}
		}
	}
}
type destinationRule struct {
	metav1.ObjectMeta `json:"metadata"`
	Spec              struct {
		Host    string
		Subsets []struct {
			Name   string
			Labels map[string]string
		}
	}
}

// HTTP transport is injectable for fixture tests; production callers supply only
// the fixed in-cluster endpoint and mounted controller identity.
func readScope(client *http.Client, base, token string, run *argo.AnalysisRun, c config) (snapshot, error) {
	fail := func(reason string) (snapshot, error) { return snapshot{}, errors.New(reason) }
	read := func(path string, obj any) error { return readJSON(client, base+path, token, obj) }
	nsPath := "/namespaces/" + namespace
	var ro argo.Rollout
	if read("/apis/argoproj.io/v1alpha1"+nsPath+"/rollouts/account-release", &ro) != nil {
		return fail("rollout-unavailable")
	}
	if ro.Name != "account-release" || ro.Namespace != namespace || ro.UID == "" || ro.DeletionTimestamp != nil || ro.Status.ObservedGeneration != strconv.FormatInt(ro.Generation, 10) || ro.Status.Abort || ro.Status.CurrentPodHash != c.Candidate || ro.Status.StableRS != c.Stable {
		return fail("release-changed")
	}
	owned := false
	for _, o := range run.OwnerReferences {
		if o.UID == ro.UID && o.Kind == "Rollout" && o.Name == ro.Name {
			owned = true
		}
	}
	current := ro.Status.Canary.CurrentStepAnalysisRunStatus
	if !owned || current == nil || current.Name != run.Name || ro.Status.CurrentStepIndex == nil || ro.Spec.Strategy.Canary == nil {
		return fail("analysis-not-current")
	}
	index := int(*ro.Status.CurrentStepIndex)
	steps := ro.Spec.Strategy.Canary.Steps
	if index < 0 || index >= len(steps) || steps[index].Analysis == nil {
		return fail("analysis-step-changed")
	}
	weight := int32(-1)
	for i := 0; i <= index; i++ {
		if steps[i].SetWeight != nil {
			weight = *steps[i].SetWeight
		}
	}
	if strconv.Itoa(int(weight)) != c.Stage {
		return fail("analysis-stage-changed")
	}
	var actual argo.AnalysisRun
	if read("/apis/argoproj.io/v1alpha1"+nsPath+"/analysisruns/"+run.Name, &actual) != nil || actual.Name != run.Name || actual.Namespace != namespace || actual.UID != run.UID || actual.DeletionTimestamp != nil || !actual.CreationTimestamp.Equal(&run.CreationTimestamp) {
		return fail("analysis-run-changed")
	}
	var vs virtualService
	var dr destinationRule
	if read("/apis/networking.istio.io/v1"+nsPath+"/virtualservices/account-release", &vs) != nil || read("/apis/networking.istio.io/v1"+nsPath+"/destinationrules/account-release", &dr) != nil {
		return fail("routing-unavailable")
	}
	if vs.UID == "" || dr.UID == "" || vs.Name != ro.Name || dr.Name != ro.Name || vs.Namespace != namespace || dr.Namespace != namespace || len(vs.Spec.Hosts) != 1 || vs.Spec.Hosts[0] != serviceHost || len(vs.Spec.Gateways) != 1 || vs.Spec.Gateways[0] != "mesh" || len(vs.Spec.HTTP) != 1 || vs.Spec.HTTP[0].Name != "primary" || len(vs.Spec.HTTP[0].Route) != 2 {
		return fail("route-scope-invalid")
	}
	weights := map[string]int{}
	for _, route := range vs.Spec.HTTP[0].Route {
		if route.Destination.Host != serviceHost || route.Destination.Port.Number != 8080 {
			return fail("route-target-invalid")
		}
		if _, duplicate := weights[route.Destination.Subset]; duplicate {
			return fail("route-duplicate")
		}
		weights[route.Destination.Subset] = route.Weight
	}
	if weights["stable"] != 100-int(weight) || weights["canary"] != int(weight) || len(weights) != 2 {
		return fail("route-stage-changed")
	}
	if dr.Spec.Host != serviceHost || len(dr.Spec.Subsets) != 2 {
		return fail("subsets-invalid")
	}
	hashes := map[string]string{}
	for _, subset := range dr.Spec.Subsets {
		if len(subset.Labels) != 2 || subset.Labels["app"] != "account-release" {
			return fail("subset-selector-invalid")
		}
		if _, duplicate := hashes[subset.Name]; duplicate {
			return fail("subset-duplicate")
		}
		hashes[subset.Name] = subset.Labels["rollouts-pod-template-hash"]
	}
	if hashes["stable"] != c.Stable || hashes["canary"] != c.Candidate {
		return fail("subset-hash-changed")
	}
	var pods core.PodList
	var sets apps.ReplicaSetList
	if read("/api/v1"+nsPath+"/pods?labelSelector=app%3Daccount-release", &pods) != nil || read("/apis/apps/v1"+nsPath+"/replicasets?labelSelector=app%3Daccount-release", &sets) != nil {
		return fail("pods-unavailable")
	}
	roles := map[string]role{}
	owners := map[string]string{}
	templates := map[string]core.PodTemplateSpec{}
	expected := map[string]int{}
	for _, rs := range sets.Items {
		hash := rs.Labels["rollouts-pod-template-hash"]
		name := ""
		if hash == c.Stable {
			name = "stable"
		}
		if hash == c.Candidate {
			name = "candidate"
		}
		if rs.Spec.Replicas == nil {
			return fail("replicas-unknown")
		}
		if name == "" {
			if *rs.Spec.Replicas != 0 {
				return fail("unexpected-active-revision")
			}
			continue
		}
		owned := false
		for _, o := range rs.OwnerReferences {
			if o.UID == ro.UID && o.Kind == "Rollout" && o.Name == ro.Name {
				owned = true
			}
		}
		if !owned || rs.UID == "" || rs.Namespace != namespace || rs.DeletionTimestamp != nil || rs.Status.ObservedGeneration != rs.Generation || *rs.Spec.Replicas != rs.Status.Replicas || *rs.Spec.Replicas != rs.Status.ReadyReplicas {
			return fail("replicas-not-ready")
		}
		owners[string(rs.UID)] = name
		templates[string(rs.UID)] = rs.Spec.Template
		expected[name] += int(*rs.Spec.Replicas)
	}
	seen := map[string]bool{}
	for _, pod := range pods.Items {
		if pod.DeletionTimestamp != nil {
			continue
		}
		name, owner := "", ""
		for _, o := range pod.OwnerReferences {
			if o.Kind == "ReplicaSet" {
				owner = string(o.UID)
				name = owners[owner]
			}
		}
		if name == "" || pod.Namespace != namespace || pod.UID == "" || seen[string(pod.UID)] {
			return fail("unexpected-pod")
		}
		seen[string(pod.UID)] = true
		ready := false
		for _, condition := range pod.Status.Conditions {
			if condition.Type == core.PodReady && condition.Status == core.ConditionTrue {
				ready = true
			}
		}
		if !ready || pod.Status.Phase != core.PodRunning {
			return fail("pod-not-ready")
		}
		image := ""
		for _, container := range pod.Spec.Containers {
			if container.Name == "api" {
				image = container.Image
			}
		}
		var api *core.ContainerStatus
		for i := range pod.Status.ContainerStatuses {
			if pod.Status.ContainerStatuses[i].Name == "api" {
				api = &pod.Status.ContainerStatuses[i]
			}
		}
		source := pod.Annotations["learning.local/source-commit"]
		template := templates[owner]
		if !matchesTemplate(image, source, template) || (name == "candidate" && !matchesTemplate(image, source, ro.Spec.Template)) {
			return fail("pod-desired-template-mismatch")
		}
		if !imagePattern.MatchString(image) || api == nil || !api.Ready || api.State.Running == nil || strings.TrimPrefix(api.ImageID, "docker-pullable://") != image || !sourcePattern.MatchString(source) || pod.Annotations["learning.local/image"] != image {
			return fail("pod-image-unverified")
		}
		hash := pod.Labels["rollouts-pod-template-hash"]
		expectedHash := c.Stable
		if name == "candidate" {
			expectedHash = c.Candidate
		}
		r := roles[name]
		if hash != expectedHash || (r.Count > 0 && (r.Hash != hash || r.Image != image)) {
			return fail("pod-role-mismatch")
		}
		r.Hash = hash
		r.Image = image
		r.Count++
		r.Pods = append(r.Pods, podIdentity{UID: string(pod.UID), Owner: owner, ImageID: api.ImageID, Source: source, Restarts: api.RestartCount, Started: api.State.Running.StartedAt.UTC().Format(time.RFC3339Nano)})
		roles[name] = r
	}
	if expected["candidate"] < 1 || roles["candidate"].Count != expected["candidate"] || roles["stable"].Count != expected["stable"] {
		return fail("pod-coverage-incomplete")
	}
	if roles["stable"].Count > 0 && roles["stable"].Image == roles["candidate"].Image {
		return fail("images-not-distinct")
	}
	for name, r := range roles {
		sort.Slice(r.Pods, func(i, j int) bool { return r.Pods[i].UID < r.Pods[j].UID })
		roles[name] = r
	}
	identity, _ := json.Marshal([]any{ro.UID, ro.Generation, ro.Status.CurrentStepIndex, current.Name, actual.UID, actual.Spec, vs.UID, vs.Spec, dr.UID, dr.Spec})
	return snapshot{Identity: string(identity), Roles: roles}, nil
}

func matchesTemplate(image, source string, template core.PodTemplateSpec) bool {
	apiCount := 0
	for _, container := range template.Spec.Containers {
		if container.Name == "api" {
			apiCount++
			if container.Image != image {
				return false
			}
		}
	}
	return apiCount == 1 && template.Annotations["learning.local/image"] == image && template.Annotations["learning.local/source-commit"] == source
}

func sdkCoverage(roles map[string]role, at time.Time) error {
	return verifyCoverage(roles, at, queryVector)
}
func verifyCoverage(roles map[string]role, at time.Time, read func(string, time.Time) ([]promSample, error)) error {
	scope := `job="account-backend",account_namespace="` + namespace + `",learning_service="account"`
	hashes := []string{}
	pods := map[string]podIdentity{}
	revisions := map[string]string{}
	for _, r := range roles {
		hashes = append(hashes, r.Hash)
		for _, p := range r.Pods {
			pods[p.UID] = p
			revisions[p.UID] = r.Hash
		}
	}
	sort.Strings(hashes)
	if len(pods) == 0 {
		return errors.New("pod-identities-missing")
	}
	scope += `,rollout_revision=~"` + strings.Join(hashes, "|") + `"`
	metadata, err := read("target_info{"+scope+"}", at)
	if err != nil || len(metadata) != len(pods) {
		return errors.New("target-info-incomplete")
	}
	instances := map[string]string{}
	seen := map[string]bool{}
	for _, row := range metadata {
		uid := row.Labels["k8s_pod_uid"]
		p, ok := pods[uid]
		instance := row.Labels["instance"]
		if !ok || instance == "" || seen[uid] || instances[instance] != "" || row.Labels["rollout_revision"] != revisions[uid] || row.Labels["service_version"] != p.Source || row.Value != 1 {
			return errors.New("target-info-mismatch")
		}
		instances[instance] = uid
		seen[uid] = true
	}
	for _, expression := range []string{"pe_go_goroutines{" + scope + "}", "timestamp(pe_go_goroutines{" + scope + "})", "timestamp(target_info{" + scope + "})"} {
		rows, e := read(expression, at)
		if e != nil || len(rows) != len(instances) {
			return errors.New("sdk-instance-coverage")
		}
		seen := map[string]bool{}
		for _, row := range rows {
			instance := row.Labels["instance"]
			uid := instances[instance]
			if uid == "" || seen[instance] || row.Labels["rollout_revision"] != revisions[uid] || row.Value < 0 {
				return errors.New("sdk-instance-mismatch")
			}
			seen[instance] = true
			if strings.HasPrefix(expression, "timestamp(") && (float64(at.Unix())-row.Value < 0 || float64(at.Unix())-row.Value >= 90) {
				return fmt.Errorf("sdk-sample-stale")
			}
		}
	}
	return nil
}
