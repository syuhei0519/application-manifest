// Argo CD と Kubernetes の更新処理に試験入力を渡し、経路設定の差分がどう扱われるかを確認します。
package controller

import (
	"encoding/json"
	"os"
	"testing"

	jsonpatch "github.com/evanphx/json-patch"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/util/jsonmergepatch"
)

func equal(a, b any) bool {
	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	return string(x) == string(y)
}

func normalized(t *testing.T, obj *unstructured.Unstructured) *unstructured.Unstructured {
	t.Helper()
	data, _ := obj.MarshalJSON()
	// Same pinned normalizer library and individual JSON-pointer remove operations.
	pointers := []string{"/spec/subsets/0/labels/rollouts-pod-template-hash", "/spec/subsets/1/labels/rollouts-pod-template-hash"}
	if obj.GetKind() == "VirtualService" {
		pointers = []string{"/spec/http/0/route/0/weight", "/spec/http/0/route/1/weight"}
	}
	for _, pointer := range pointers {
		raw, _ := json.Marshal([]map[string]string{{"op": "remove", "path": pointer}})
		patch, err := jsonpatch.DecodePatch(raw)
		if err != nil {
			t.Fatal(err)
		}
		if updated, err := patch.Apply(data); err == nil {
			data = updated
		}
	}
	result := &unstructured.Unstructured{}
	if err := result.UnmarshalJSON(data); err != nil {
		t.Fatal(err)
	}
	return result
}

func applySync(t *testing.T, desired, live *unstructured.Unstructured) *unstructured.Unstructured {
	t.Helper()
	nt, nl := normalized(t, desired), normalized(t, live)
	// Exact pinned Argo CD functions, extracted verbatim at test time.
	patch, err := getMergePatch(nl, live, nil)
	if err != nil {
		t.Fatal(err)
	}
	target, err := applyMergePatch(nt, patch, nil)
	if err != nil {
		t.Fatal(err)
	}
	restoreNonIgnoredFields(target.Object, desired.Object, nt.Object, nl.Object)
	// Actual Kubernetes client-side three-way apply of the corrected target.
	original, _ := desired.MarshalJSON()
	modified, _ := target.MarshalJSON()
	current, _ := live.MarshalJSON()
	apply, err := jsonmergepatch.CreateThreeWayJSONMergePatch(original, modified, current)
	if err != nil {
		t.Fatal(err)
	}
	result, err := jsonpatch.MergePatch(current, apply)
	if err != nil {
		t.Fatal(err)
	}
	obj := &unstructured.Unstructured{}
	if err = json.Unmarshal(result, &obj.Object); err != nil {
		t.Fatal(err)
	}
	return obj
}

func TestIstioSyncOwnership(t *testing.T) {
	data, err := os.ReadFile("fixtures.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures []map[string]any
	if err = json.Unmarshal(data, &fixtures); err != nil {
		t.Fatal(err)
	}
	for _, fixture := range fixtures {
		desired := &unstructured.Unstructured{Object: fixture}
		t.Run(desired.GetKind(), func(t *testing.T) {
			for _, w := range []int64{10, 50, 100, 0} {
				live := desired.DeepCopy()
				if live.GetKind() == "DestinationRule" {
					subsets, _, _ := unstructured.NestedSlice(live.Object, "spec", "subsets")
					for i, h := range []string{"stablehash", "canaryhash"} {
						subsets[i].(map[string]any)["labels"].(map[string]any)["rollouts-pod-template-hash"] = h
					}
					_ = unstructured.SetNestedSlice(live.Object, subsets, "spec", "subsets")
				} else {
					http, _, _ := unstructured.NestedSlice(live.Object, "spec", "http")
					routes := http[0].(map[string]any)["route"].([]any)
					routes[0].(map[string]any)["weight"] = 100 - w
					routes[1].(map[string]any)["weight"] = w
					_ = unstructured.SetNestedSlice(live.Object, http, "spec", "http")
				}
				result := applySync(t, desired, live)
				if !equal(result.Object, live.Object) {
					t.Fatalf("runtime ownership lost at %d%%", w)
				}
				if live.GetKind() == "DestinationRule" {
					// Preserve the historical counterexample: absent desired hash makes restore reset the array.
					legacy := normalized(t, desired)
					broken := applySync(t, legacy, live)
					if equal(broken.Object, live.Object) {
						t.Fatal("legacy missing-hash counterexample no longer reproduces")
					}
				}
				// Array collateral is a pinned-version limitation, NOT ignored drift. It must remain OutOfSync.
				drift := live.DeepCopy()
				if live.GetKind() == "DestinationRule" {
					subsets, _, _ := unstructured.NestedSlice(drift.Object, "spec", "subsets")
					subsets[0].(map[string]any)["labels"].(map[string]any)["app"] = "wrong-workload"
					_ = unstructured.SetNestedSlice(drift.Object, subsets, "spec", "subsets")
				} else {
					http, _, _ := unstructured.NestedSlice(drift.Object, "spec", "http")
					http[0].(map[string]any)["route"].([]any)[0].(map[string]any)["destination"].(map[string]any)["port"] = map[string]any{"number": int64(9090)}
					_ = unstructured.SetNestedSlice(drift.Object, http, "spec", "http")
				}
				if equal(normalized(t, desired).Object, normalized(t, drift).Object) {
					t.Fatal("non-ignored configuration drift hidden")
				}
				synced := applySync(t, desired, drift)
				if equal(normalized(t, desired).Object, normalized(t, synced).Object) {
					t.Fatal("expected array drift to remain OutOfSync on pinned version")
				}
			}
		})
	}
}
