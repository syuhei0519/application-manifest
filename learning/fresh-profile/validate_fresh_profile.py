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
SQL = HERE / "workload" / "account-role-bootstrap.sql"
KUSTOMIZATION = HERE / "workload" / "kustomization.yaml"
DB_CASES = HERE / "workload" / "db-permission-cases.json"
RELEASE = HERE / "release-definition"


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


def rendered_workload(workload_path=WORKLOAD, sql_path=SQL, kustomization_path=KUSTOMIZATION):
    """Model the one deterministic configMapGenerator used by this profile."""
    documents = list(yaml.safe_load_all(Path(workload_path).read_text(encoding="utf-8")))
    kustomization = yaml.safe_load(Path(kustomization_path).read_text(encoding="utf-8"))
    require(kustomization == {
        "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
        "resources": ["account.yaml"],
        "configMapGenerator": [{"name": "account-role-bootstrap", "namespace": "fresh-account",
                                "files": ["bootstrap.sql=account-role-bootstrap.sql"]}],
        "generatorOptions": {"disableNameSuffixHash": True},
    }, "kustomization-contract")
    sql = Path(sql_path).read_text(encoding="utf-8")
    documents.append({"apiVersion": "v1", "kind": "ConfigMap",
                      "metadata": {"name": "account-role-bootstrap", "namespace": "fresh-account"},
                      "data": {"bootstrap.sql": sql}})
    return documents


def validate_release_chart(release_path=RELEASE):
    root = Path(release_path)
    chart = yaml.safe_load((root / "Chart.yaml").read_text(encoding="utf-8"))
    values = yaml.safe_load((root / "values.yaml").read_text(encoding="utf-8"))
    template = (root / "templates" / "application.yaml").read_text(encoding="utf-8")
    require(chart.get("name") == "fresh-account-release-definition", "release-chart-name")
    require(values == {"workloadRevision": "", "accountRuntimeImage": ""}, "release-values")
    require("__APPLICATION_MANIFEST_SHA__" not in template, "release-self-token")
    for fragment in (
        'required "workloadRevision is required"',
        'regexMatch "^[0-9a-f]{40}$"',
        'required "accountRuntimeImage is required"',
        "name: fresh-account-release", "namespace: fresh-control",
        "repoURL: https://github.com/syuhei0519/application-manifest.git",
        "path: learning/fresh-profile/workload", "project: fresh-account",
        "name: fresh-account-sandbox", "namespace: fresh-account",
        "ghcr.io/syuhei0519/backend-app=",
    ):
        require(fragment in template, f"release-template:{fragment}")
    return {"chart": chart, "values": values, "template": template}


def validate(contract_path=CONTRACT, workload_path=WORKLOAD, release_path=RELEASE,
             sql_path=SQL, kustomization_path=KUSTOMIZATION, db_cases_path=DB_CASES):
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    documents = rendered_workload(workload_path, sql_path, kustomization_path)
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
    require(account_image["freshProfileCompatible"] is False and account_image["baselineOnly"] is True,
            "account-profile-compatibility")
    fresh_requirement = contract["images"]["freshRuntimeRequirement"]
    require(fresh_requirement == {
        "sourceAtOrAfter": "d797fc285618dea3eb9f64b425793eaf1e2a7012",
        "requiredConfig": "PLAT-211 dual-profile namespace and Downward API support",
        "immutableImageRef": "PENDING_VERIFIED_PLAT211_IMAGE",
        "freshEligible": False,
        "blocker": "verified main CI image digest/source/config/platform/entrypoint and retained G2 source are not yet available"
    }, "fresh-runtime-requirement")
    found_images = {container["image"] for item in documents if pod_spec(item)
                    for container in pod_spec(item).get("containers", [])}
    require(account_image["ref"] in found_images, "account-image")
    require(contract["images"]["postgres"]["ref"] in found_images, "postgres-image")
    require(not any("966adba896524519" in image for image in found_images), "rollouts-image-reuse")
    rollout = index["Rollout/fresh-account/account-api"]
    runtime_env = {item["name"]: item for item in
                   rollout["spec"]["template"]["spec"]["containers"][0]["env"]}
    require(runtime_env["PLATFORM_LAB_PROFILE"].get("value") == "fresh-account", "telemetry-profile")
    require(runtime_env["POD_UID"].get("valueFrom", {}).get("fieldRef", {}).get("fieldPath") == "metadata.uid",
            "telemetry-pod-uid")
    require("$(PLATFORM_LAB_PROFILE)" in runtime_env["OTEL_RESOURCE_ATTRIBUTES"].get("value", "") and
            "$(POD_UID)" in runtime_env["OTEL_RESOURCE_ATTRIBUTES"].get("value", ""), "telemetry-attributes")

    pvc = index["PersistentVolumeClaim/fresh-account/account-data"]
    require(pvc["metadata"]["annotations"]["argocd.argoproj.io/sync-options"] ==
            "Prune=false,Delete=false", "pvc-retention")
    require(contract["dataMode"] == "new-empty-persistent-volume", "data-mode")

    generated = index["ConfigMap/fresh-account/account-role-bootstrap"]["data"]["bootstrap.sql"]
    require(generated == Path(sql_path).read_text(encoding="utf-8"), "generated-sql-drift")
    require(sum(1 for item in documents if item.get("metadata", {}).get("name") == "account-role-bootstrap") == 1,
            "sql-single-source")
    jobs = {item["metadata"]["name"]: item for item in documents if item["kind"] == "Job"}
    for name, wave in (("account-db-role-bootstrap", "10"), ("account-migration", "20")):
        annotations = jobs[name]["metadata"]["annotations"]
        require(annotations == {"argocd.argoproj.io/sync-wave": wave,
                                "argocd.argoproj.io/sync-options": "Prune=false,Delete=false"},
                f"job-order:{name}")
        require(jobs[name]["spec"]["template"]["metadata"]["annotations"] ==
                {"sidecar.istio.io/inject": "false"}, f"job-sidecar:{name}")
    require("--set=database_name=account" in jobs["account-db-role-bootstrap"]["spec"]["template"]["spec"]["containers"][0]["args"][0]
            and "--set=service_schema=account" in jobs["account-db-role-bootstrap"]["spec"]["template"]["spec"]["containers"][0]["args"][0],
            "role-bootstrap-variables")

    cases = json.loads(Path(db_cases_path).read_text(encoding="utf-8"))
    expected_cases = {
        ("migration-ddl-owned-schema", "account_migrator", "DDL", "account", "allow"),
        ("runtime-dml-owned-schema", "account_runtime", "DML", "account", "allow"),
        ("runtime-ddl-owned-schema", "account_runtime", "DDL", "account", "deny"),
        ("runtime-other-schema", "account_runtime", "DML", "other_service", "deny"),
        ("migration-other-schema", "account_migrator", "DDL", "other_service", "deny"),
    }
    actual_cases = {(case["id"], case["role"], case["operation"], case["target"], case["expected"])
                    for case in cases.get("requiredLiveCases", [])}
    require(actual_cases == expected_cases and cases.get("status") == "required-unrun", "db-permission-cases")
    release = validate_release_chart(release_path)
    return contract, documents, release


if __name__ == "__main__":
    contract, documents, release = validate()
    print("fresh workload valid",
          f"resources={len(documents)}",
          f"image={contract['images']['accountRuntime']['ref'].split('@')[1]}",
          "release=parameterized-chart")
