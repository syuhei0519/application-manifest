// render済みYAMLからすべての対象PodSpec/container imageとrelease注釈を抽出する固定parser。
// ci/trusted-render.pyが呼び、checksum付きinventory JSONを出す。Chartの見た目だけでimage更新を判定しない。
// Inventory is data-only: no network, subprocess, hook or plugin execution.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

type Entry struct {
	Namespace     string            `json:"namespace"`
	Kind          string            `json:"kind"`
	Name          string            `json:"name"`
	PodSpecPath   string            `json:"podSpecPath"`
	ContainerType string            `json:"containerType"`
	ContainerName string            `json:"containerName"`
	Repository    string            `json:"repository"`
	Tag           string            `json:"tag"`
	Digest        string            `json:"digest"`
	Release       map[string]string `json:"release"`
}
type Output struct {
	Schema          string  `json:"schema"`
	RenderSHA256    string  `json:"renderSha256"`
	InventorySHA256 string  `json:"inventorySha256"`
	Entries         []Entry `json:"entries"`
}

func object(v any) map[string]any           { m, _ := v.(map[string]any); return m }
func str(m map[string]any, k string) string { s, _ := m[k].(string); return s }
func hash(data []byte) string               { s := sha256.Sum256(data); return hex.EncodeToString(s[:]) }
func path(root map[string]any, parts ...string) map[string]any {
	for _, p := range parts {
		root = object(root[p])
		if root == nil {
			return nil
		}
	}
	return root
}

func imageParts(image string) (string, string, string, error) {
	parts := strings.Split(image, "@")
	if len(parts) != 2 || !strings.HasPrefix(parts[1], "sha256:") || len(parts[1]) != 71 {
		return "", "", "", fmt.Errorf("image must contain fixed SHA256 digest")
	}
	if b, err := hex.DecodeString(parts[1][7:]); err != nil || len(b) != 32 {
		return "", "", "", fmt.Errorf("invalid image digest")
	}
	tagged := parts[0]
	split := strings.LastIndex(tagged, ":")
	if split <= strings.LastIndex(tagged, "/") || split < 1 || split == len(tagged)-1 {
		return "", "", "", fmt.Errorf("explicit repository and version/SHA tag required")
	}
	repo, tag := tagged[:split], tagged[split+1:]
	if tag == "latest" || strings.ContainsAny(image, " \t\r\n") {
		return "", "", "", fmt.Errorf("floating/invalid image")
	}
	return repo, tag, parts[1], nil
}

func inventory(data []byte, namespace string) (Output, error) {
	result := Output{Schema: "core-platform/image-inventory/v1", RenderSHA256: hash(data), Entries: []Entry{}}
	if len(data) > 20<<20 {
		return result, fmt.Errorf("render exceeds 20 MiB")
	}
	decoder := yaml.NewDecoder(bytes.NewReader(data))
	identities := map[string]bool{}
	resources := map[string]bool{}
	for docs := 0; ; docs++ {
		if docs > 5000 {
			return result, fmt.Errorf("too many YAML documents")
		}
		var r map[string]any
		err := decoder.Decode(&r)
		if err == io.EOF {
			break
		}
		if err != nil {
			return result, fmt.Errorf("YAML parse: %w", err)
		}
		if len(r) == 0 {
			continue
		}
		kind := str(r, "kind")
		api := str(r, "apiVersion")
		meta := object(r["metadata"])
		name := str(meta, "name")
		ns := str(meta, "namespace")
		if ns == "" {
			ns = namespace
		}
		if name == "" || api == "" {
			return result, fmt.Errorf("resource identity missing")
		}
		resourceKey := ns + "/" + api + "/" + kind + "/" + name
		if resources[resourceKey] {
			return result, fmt.Errorf("duplicate resource %s", resourceKey)
		}
		resources[resourceKey] = true
		var spec, podMeta map[string]any
		specPath := ""
		switch api + "/" + kind {
		case "v1/Pod":
			spec = path(r, "spec")
			podMeta = meta
			specPath = "spec"
		case "apps/v1/Deployment", "apps/v1/StatefulSet", "apps/v1/DaemonSet", "apps/v1/ReplicaSet", "v1/ReplicationController", "batch/v1/Job":
			spec = path(r, "spec", "template", "spec")
			podMeta = path(r, "spec", "template", "metadata")
			specPath = "spec.template.spec"
		case "batch/v1/CronJob":
			spec = path(r, "spec", "jobTemplate", "spec", "template", "spec")
			podMeta = path(r, "spec", "jobTemplate", "spec", "template", "metadata")
			specPath = "spec.jobTemplate.spec.template.spec"
		case "v1/ConfigMap", "v1/Service", "v1/ServiceAccount", "v1/PersistentVolumeClaim", "networking.k8s.io/v1/NetworkPolicy", "policy/v1/PodDisruptionBudget", "autoscaling/v2/HorizontalPodAutoscaler":
			continue
		default:
			return result, fmt.Errorf("unclassified resource kind %s/%s; review required", api, kind)
		}
		if spec == nil {
			return result, fmt.Errorf("PodSpec missing at %s %s", resourceKey, specPath)
		}
		annotations := object(podMeta["annotations"])
		release := map[string]string{}
		for k, v := range annotations {
			if strings.HasPrefix(k, "account.lab/") && k != "account.lab/restart-nonce" {
				value, ok := v.(string)
				if !ok {
					return result, fmt.Errorf("release annotation %s must be string", k)
				}
				release[k] = value
			}
		}
		for _, containerType := range []string{"containers", "initContainers", "ephemeralContainers"} {
			raw, exists := spec[containerType]
			if !exists {
				if containerType == "containers" {
					return result, fmt.Errorf("containers missing at %s", resourceKey)
				}
				continue
			}
			containers, ok := raw.([]any)
			if !ok || (containerType == "containers" && len(containers) == 0) {
				return result, fmt.Errorf("invalid %s at %s", containerType, resourceKey)
			}
			for _, item := range containers {
				c := object(item)
				containerName := str(c, "name")
				if containerName == "" {
					return result, fmt.Errorf("container name missing")
				}
				repo, tag, digest, err := imageParts(str(c, "image"))
				if err != nil {
					return result, fmt.Errorf("%s/%s: %w", resourceKey, containerName, err)
				}
				identity := resourceKey + "/" + specPath + "/" + containerType + "/" + containerName
				if identities[identity] {
					return result, fmt.Errorf("duplicate container %s", identity)
				}
				identities[identity] = true
				result.Entries = append(result.Entries, Entry{ns, kind, name, specPath, containerType, containerName, repo, tag, digest, release})
				if len(result.Entries) > 1000 {
					return result, fmt.Errorf("inventory exceeds 1000 containers")
				}
			}
		}
	}
	if len(result.Entries) == 0 {
		return result, fmt.Errorf("no PodSpec inventory")
	}
	sort.Slice(result.Entries, func(i, j int) bool {
		a, _ := json.Marshal(result.Entries[i])
		b, _ := json.Marshal(result.Entries[j])
		return string(a) < string(b)
	})
	canonical, _ := json.Marshal(result.Entries)
	result.InventorySHA256 = hash(canonical)
	return result, nil
}
func main() {
	data, err := io.ReadAll(io.LimitReader(os.Stdin, (20<<20)+1))
	if err == nil {
		var out Output
		out, err = inventory(data, "account")
		if err == nil {
			err = json.NewEncoder(os.Stdout).Encode(out)
		}
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
