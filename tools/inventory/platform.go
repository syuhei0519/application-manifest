package main

import "fmt"

const postgresRepository = "docker.io/library/postgres"
const postgresTag = "17.10-bookworm"
const postgresDigest = "sha256:9b18b78397054fce88a9552e9d5a3ad5bb7fd258c5b3cc1c5028e46373d6ea8f"

// Platform images have their own immutable policy; they are not GHCR app releases.
func platformPolicy(r, spec, c map[string]any, repo, tag, digest, containerType string) error {
	meta := object(r["metadata"])
	name := str(meta, "name")
	// Bind both reserved workload names before classifying the image family.
	// Changing a DB image must never route that identity into app provenance.
	fixedDB := name == "postgresql" || name == "order-postgresql"
	if !fixedDB && repo != postgresRepository {
		return nil
	}
	if repo != postgresRepository || tag != postgresTag || digest != postgresDigest ||
		str(r, "kind") != "StatefulSet" || !fixedDB ||
		containerType != "containers" || str(c, "name") != name {
		return fmt.Errorf("platform image identity rejected")
	}
	pod := object(spec["securityContext"])
	security := object(c["securityContext"])
	if spec["automountServiceAccountToken"] != false || pod["runAsNonRoot"] != true ||
		pod["runAsUser"] != 999 || pod["runAsGroup"] != 999 || pod["fsGroup"] != 999 ||
		str(object(pod["seccompProfile"]), "type") != "RuntimeDefault" ||
		security["allowPrivilegeEscalation"] != false || security["readOnlyRootFilesystem"] != true {
		return fmt.Errorf("platform security context rejected")
	}
	// Container settings override Pod defaults. Keep the fixed Pod policy above,
	// and accept only absent (inherited) or equivalent explicit overrides. Do not
	// treat malformed/null overrides as absent in this untrusted rendered input.
	for _, key := range []string{"runAsNonRoot", "runAsUser", "runAsGroup"} {
		if value, exists := security[key]; exists && value != pod[key] {
			return fmt.Errorf("platform container security override rejected: %s", key)
		}
	}
	if value, exists := security["seccompProfile"]; exists && str(object(value), "type") != "RuntimeDefault" {
		return fmt.Errorf("platform container seccomp override rejected")
	}
	if v, ok := security["privileged"]; ok && v != false {
		return fmt.Errorf("privileged platform rejected")
	}
	caps := object(security["capabilities"])
	drop, ok := caps["drop"].([]any)
	if !ok || len(drop) != 1 || drop[0] != "ALL" || caps["add"] != nil {
		return fmt.Errorf("platform capabilities rejected")
	}
	if str(object(meta["annotations"]), "argocd.argoproj.io/sync-options") != "Prune=confirm,Delete=confirm" ||
		object(r["spec"])["volumeClaimTemplates"] != nil {
		return fmt.Errorf("platform StatefulSet retention rejected")
	}
	volumes, _ := spec["volumes"].([]any)
	found := false
	for _, value := range volumes {
		v := object(value)
		if v["hostPath"] != nil {
			return fmt.Errorf("hostPath rejected")
		}
		if str(v, "name") == "data" {
			found = str(object(v["persistentVolumeClaim"]), "claimName") == name+"-data"
		}
	}
	if !found {
		return fmt.Errorf("fixed platform PVC required")
	}
	return nil
}

func pvcPolicy(r map[string]any) error {
	meta := object(r["metadata"])
	name := str(meta, "name")
	if name == "postgresql-data" || name == "order-postgresql-data" {
		if str(object(meta["annotations"]), "argocd.argoproj.io/sync-options") != "Prune=false,Delete=false" {
			return fmt.Errorf("platform PVC retention rejected")
		}
	}
	return nil
}

// Reject PodSpecs hidden in otherwise non-workload resources or unexpected paths.
func podSpecPaths(value any, prefix string) []string {
	var paths []string
	switch v := value.(type) {
	case map[string]any:
		for key, child := range v {
			if key == "containers" || key == "initContainers" || key == "ephemeralContainers" {
				if _, ok := child.([]any); ok {
					paths = append(paths, prefix)
				}
			}
			next := key
			if prefix != "" {
				next = prefix + "." + key
			}
			paths = append(paths, podSpecPaths(child, next)...)
		}
	case []any:
		for _, child := range v {
			paths = append(paths, podSpecPaths(child, prefix+"[]")...)
		}
	}
	return paths
}
