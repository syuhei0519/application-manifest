package main

import (
	"fmt"
	"github.com/google/go-containerregistry/pkg/authn"
	"github.com/google/go-containerregistry/pkg/name"
	v1 "github.com/google/go-containerregistry/pkg/v1"
	"github.com/google/go-containerregistry/pkg/v1/layout"
	"github.com/google/go-containerregistry/pkg/v1/remote"
	"os"
)

const ref = "ghcr.io/syuhei0519/argo-rollouts-account-policy:src-c879ac072bf7-bin-02cdb9d92d55"
const expected = "sha256:3db562b99a4b200285eaf756a03dc8d90c5d00c7579e2802ab6fccb50d65cdec"

func noRetry(error) bool { return false }

func writeImage(tag name.Tag, image v1.Image, opts ...remote.Option) error {
	opts = append(opts, remote.WithRetryPredicate(noRetry), remote.WithRetryStatusCodes())
	return remote.Write(tag, image, opts...)
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "layout required")
		os.Exit(1)
	}
	path, err := layout.FromPath(os.Args[1])
	if err != nil {
		fail()
	}
	index, err := path.ImageIndex()
	if err != nil {
		fail()
	}
	m, err := index.IndexManifest()
	if err != nil || len(m.Manifests) != 1 || m.Manifests[0].Digest.String() != expected {
		fail()
	}
	image, err := index.Image(m.Manifests[0].Digest)
	if err != nil {
		fail()
	}
	tag, err := name.NewTag(ref)
	if err != nil {
		fail()
	}
	if err = writeImage(tag, image, remote.WithAuthFromKeychain(authn.DefaultKeychain)); err != nil {
		fail()
	}
	fmt.Println(expected)
}

func fail() { fmt.Fprintln(os.Stderr, "registry write failed or outcome uncertain"); os.Exit(1) }
