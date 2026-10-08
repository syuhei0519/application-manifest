package release

import (
	"strings"
	"testing"
	"time"
)

func validSBOMFixture() ([]byte, []byte, string, time.Time) {
	config := "sha256:" + strings.Repeat("a", 64)
	b := []byte(`{"bomFormat":"CycloneDX","specVersion":"1.7","version":1,"metadata":{"timestamp":"2026-10-03T04:00:00Z","tools":{"components":[{"type":"application","name":"trivy","version":"0.75.0"}]},"component":{"type":"container","name":".oci/layout","bom-ref":"image-root","properties":[{"name":"aquasecurity:trivy:ImageID","value":"` + config + `"}]}},"components":[{"type":"library","name":"nginx","version":"1.29.0","bom-ref":"nginx-ref"}],"dependencies":[{"ref":"image-root","dependsOn":["nginx-ref"]},{"ref":"nginx-ref","dependsOn":[]}]}`)
	r := []byte(`{"SchemaVersion":2,"Trivy":{"Version":"0.75.0"},"ArtifactType":"container_image","ArtifactName":".oci/layout","Metadata":{"ImageID":"` + config + `"},"Results":[{"Packages":[{"Name":"nginx","Version":"1.29.0"}]}]}`)
	return b, r, config, time.Date(2026, 10, 3, 4, 1, 0, 0, time.UTC)
}

func TestSBOMOfficialSchemaAndInventory(t *testing.T) {
	b, r, config, now := validSBOMFixture()
	p, e := ValidateSBOM(b, r, config, now)
	if e != nil || p.ComponentCount != 1 || p.ScannedPackageCount != 1 || p.SHA256 != Checksum(b) || p.SpecVersion != "1.7" {
		t.Fatal("valid schema and inventory rejected")
	}
	for name, bad := range map[string][]byte{
		"wrong-format":           []byte(strings.Replace(string(b), "CycloneDX", "SPDX", 1)),
		"unknown-field":          []byte(strings.Replace(string(b), `"version":1`, `"version":1,"unexpected":"SECRET_SENTINEL"`, 1)),
		"invalid-component-type": []byte(strings.Replace(string(b), `"type":"library"`, `"type":"invalid"`, 1)),
		"missing-inventory":      []byte(strings.Replace(string(b), `"name":"nginx"`, `"name":"other"`, 1)),
		"different-version":      []byte(strings.Replace(string(b), "1.29.0", "1.28.0", 1)),
		"dangling-dependency":    []byte(strings.Replace(string(b), `"dependsOn":["nginx-ref"]`, `"dependsOn":["absent"]`, 1)),
		"missing-root-graph":     []byte(strings.Replace(string(b), `{"ref":"image-root","dependsOn":["nginx-ref"]},`, "", 1)),
		"disconnected-inventory": []byte(strings.Replace(string(b), `"dependsOn":["nginx-ref"]`, `"dependsOn":[]`, 1)),
		"config-swapped":         []byte(strings.Replace(string(b), config, "sha256:"+strings.Repeat("b", 64), 1)),
		"duplicate-key":          []byte(strings.Replace(string(b), `"name":"nginx"`, `"name":"nginx","name":"nginx"`, 1)),
		"old-sbom":               []byte(strings.Replace(string(b), "2026-10-03T04:00:00Z", "2000-01-01T00:00:00Z", 1)),
		"wrong-tool":             []byte(strings.Replace(string(b), "0.75.0", "0.74.0", 1)),
		"empty-bom":              []byte(`{"bomFormat":"CycloneDX","specVersion":"1.7"}`),
	} {
		t.Run(name, func(t *testing.T) {
			if _, e := ValidateSBOM(bad, r, config, now); e == nil || e.Error() != ErrRefused.Error() {
				t.Fatal("invalid SBOM accepted or raw diagnostics exposed")
			}
		})
	}
	for _, bad := range [][]byte{
		[]byte(strings.Replace(string(r), config, "sha256:"+strings.Repeat("b", 64), 1)),
		[]byte(strings.Replace(string(r), "container_image", "filesystem", 1)),
		[]byte(strings.Replace(string(r), `"Name":"nginx","Version":"1.29.0"`, `"Name":"absent","Version":"1.0"`, 1)),
		[]byte(strings.Replace(string(r), `[{"Name":"nginx","Version":"1.29.0"}]`, `[]`, 1)),
	} {
		if _, e := ValidateSBOM(b, bad, config, now); e == nil {
			t.Fatal("unbound or incomplete report accepted")
		}
	}
}

func TestSBOMSchemaLoaderNeverLoadsExternalInput(t *testing.T) {
	if _, e := (refuseSchemaLoader{}).Load("https://external.invalid/schema"); e == nil {
		t.Fatal("external schema loader allowed")
	}
	b, r, config, now := validSBOMFixture()
	// Instance $schema is data: it cannot redirect the compiled validator.
	b = []byte(strings.Replace(string(b), `"version":1`, `"version":1,"$schema":"https://external.invalid/schema"`, 1))
	if _, e := ValidateSBOM(b, r, config, now); e != nil {
		t.Fatal("instance schema affected fixed validation")
	}
}
