package main

import (
	"github.com/google/go-containerregistry/pkg/authn"
	"github.com/google/go-containerregistry/pkg/name"
	"github.com/google/go-containerregistry/pkg/v1/empty"
	"github.com/google/go-containerregistry/pkg/v1/remote"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestManifestWriteNeverReplays(t *testing.T) {
	for _, scenario := range []string{"disconnect-after-accept", "server-503"} {
		t.Run(scenario, func(t *testing.T) {
			var puts atomic.Int32
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				t.Logf("%s %s", r.Method, r.URL.Path)
				if r.Method == http.MethodHead && strings.Contains(r.URL.Path, "/manifests/") {
					w.WriteHeader(404)
					return
				}
				if r.Method == http.MethodPut && strings.Contains(r.URL.Path, "/manifests/") {
					puts.Add(1)
					if scenario == "server-503" {
						w.WriteHeader(503)
						return
					}
					conn, _, err := w.(http.Hijacker).Hijack()
					if err != nil {
						t.Error(err)
						return
					}
					conn.Close()
					return
				}
				w.WriteHeader(http.StatusOK)
			}))
			defer srv.Close()
			tag, err := name.NewTag(strings.TrimPrefix(srv.URL, "http://")+"/test:tag", name.Insecure)
			if err != nil {
				t.Fatal(err)
			}
			if err := writeImage(tag, empty.Image, remote.WithAuth(authn.Anonymous)); err == nil {
				t.Fatal("expected write failure")
			} else {
				t.Log(err)
			}
			if got := puts.Load(); got != 1 {
				t.Fatalf("manifest PUT count=%d, want 1", got)
			}
		})
	}
}
