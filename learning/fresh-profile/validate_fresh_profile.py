#!/usr/bin/env python3
"""Validate the PLAT-214/216 fresh workload without contacting Kubernetes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


HERE = Path(__file__).resolve().parent
CONTRACT = HERE / "contract.json"
WORKLOAD = HERE / "workload" / "account.yaml"
RELEASE = HERE / "release-definition" / "fresh-account-release.yaml"
TOKEN = "__APPLICATION_MANIFEST_SHA__"


class Rejected(ValueError):
    pass


def require(value, reason):
    if not value:
        raise Rejected(reason)


def identity(document):
    metadata = document.get("metadata", {})
    return f"{document.get('kind')}/{metadata.get('namespace')}/{metadata.get('name')}"


def secret_refs(value, refs):
    if isinstance(value, dict):
        ref = value.get("secretKeyRef")
        if isinstance(ref, dict):
            refs.add((ref.get("name"), ref.get("key")))
        for child in value.values():
            secret_refs(child, refs)
    elif isinstance(value, list):
        for child in value:
            secret_refs(child, refs)


def pod_spec(document):
    if document["kind"] in {"Job", "Rollout", "StatefulSet"}:
        return document["spec"]["template"]["spec"]
    return None


def validate(contract_path=CONTRACT, workload_path=WORKLOAD, release_path=RELEASE):
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    documents = list(yaml.safe_load_all(Path(workload_path).read_text(encoding="utf-8")))
    require(all(isinstance(item, dict) for item in documents), "yaml-document")
    require(not any(item.get("kind") == "Secret" for item in documents), "secret-object-in-git")

    expected = {
        "ServiceAccount/fresh-account/account-database",
        "ServiceAccount/fresh-account/account-migration",
        "ServiceAccount/fresh-account/account-runtime",
        "ConfigMap/fresh-account/account-role-bootstrap",
        "Service/fresh-account/account-postgresql",
        "PersistentVolumeClaim/fresh-account/account-data",
        "StatefulSet/fresh-account/account-postgresql",
        "Job/fresh-account/account-db-role-bootstrap",
        "Job/fresh-account/account-migration",
        "Rollout/fresh-account/account-api",
        "Service/fresh-account/account-api",
        "DestinationRule/fresh-account/account-api",
        "VirtualService/fresh-account/account-api",
    }
    actual = {identity(item) for item in documents}
    require(actual == expected and len(documents) == len(expected), "resource-inventory")

    index = {identity(item): item for item in documents}
    refs = set()
    secret_refs(documents, refs)
    expected_refs = {
        ("account-db-bootstrap", "admin-password"),
        ("account-db-migration", "username"),
        ("account-db-migration", "password"),
        ("account-db-migration", "uri"),
        ("account-db-runtime", "username"),
        ("account-db-runtime", "password"),
        ("account-db-runtime", "uri"),
    }
    require(refs == expected_refs, "secret-reference-set")
    for item in documents:
        pod = pod_spec(item)
        if pod:
            require(pod.get("automountServiceAccountToken") is False, "service-account-token")
            require(pod.get("serviceAccountName") in {"account-database", "account-migration", "account-runtime"},
                    "service-account-name")

    account_image = contract["images"]["accountRuntime"]
    require(account_image["sourceCommit"] == "527b9bccf4ae56244d134ae384564ec1cccea815", "account-source")
    require(account_image["entrypoint"] == ["/server"] and account_image["user"] == "10001:10001",
            "account-config")
    require(account_image["ciArtifact"]["published"] is False and
            account_image["ciArtifact"]["freshEligible"] is False, "account-supply-boundary")
    found_images = {container["image"] for item in documents if pod_spec(item)
                    for container in pod_spec(item).get("containers", [])}
    require(account_image["ref"] in found_images, "account-image")
    require(contract["images"]["postgres"]["ref"] in found_images, "postgres-image")
    require(not any("966adba896524519" in image for image in found_images), "rollouts-image-reuse")

    pvc = index["PersistentVolumeClaim/fresh-account/account-data"]
    require(pvc["metadata"]["annotations"]["argocd.argoproj.io/sync-options"] ==
            "Prune=false,Delete=false", "pvc-retention")
    require(contract["dataMode"] == "new-empty-persistent-volume", "data-mode")

    release = yaml.safe_load(Path(release_path).read_text(encoding="utf-8"))
    require(identity(release) == "Application/fresh-control/fresh-account-release", "release-identity")
    require(release["spec"]["source"]["path"] == "learning/fresh-profile/workload", "release-path")
    revision = release["spec"]["source"]["targetRevision"]
    require(revision == TOKEN or re.fullmatch(r"[0-9a-f]{40}", revision), "release-revision")
    return contract, documents, release


if __name__ == "__main__":
    contract, documents, release = validate()
    print("fresh workload valid",
          f"resources={len(documents)}",
          f"image={contract['images']['accountRuntime']['ref'].split('@')[1]}",
          f"revision={release['spec']['source']['targetRevision']}")
