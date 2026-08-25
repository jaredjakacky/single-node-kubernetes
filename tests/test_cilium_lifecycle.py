#!/usr/bin/env python3

"""Validate the Cilium lifecycle's static safety and idempotence boundaries."""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent.parent
ANSIBLE_ROOT = REPOSITORY_ROOT / "ansible"
CILIUM_ROLE = ANSIBLE_ROOT / "roles" / "cilium"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_yaml(path: pathlib.Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def task_actions(tasks: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    actions = []
    for task in tasks:
        for key, value in task.items():
            if key.startswith("ansible.builtin."):
                require(isinstance(value, dict), f"{key} must use structured arguments")
                actions.append((key, value))
    return actions


def validate_lifecycle_separation() -> None:
    node = load_yaml(ANSIBLE_ROOT / "playbooks" / "node.yml")
    bootstrap = load_yaml(ANSIBLE_ROOT / "playbooks" / "bootstrap.yml")
    cilium = load_yaml(ANSIBLE_ROOT / "playbooks" / "cilium.yml")

    node_roles = [entry["role"] for entry in node[0]["roles"]]
    bootstrap_roles = [entry["role"] for entry in bootstrap[0]["roles"]]
    cilium_roles = [entry["role"] for entry in cilium[0]["roles"]]
    require("cilium" not in node_roles, "node convergence must not deploy Cilium")
    require(
        "cilium" not in bootstrap_roles,
        "kubeadm bootstrap must not deploy Cilium",
    )
    require(cilium_roles == ["cilium"], "only cilium.yml may invoke the role")

    def referenced_roles(value: Any) -> list[str]:
        if isinstance(value, list):
            return [role for item in value for role in referenced_roles(item)]
        if not isinstance(value, dict):
            return []
        roles = []
        for key, item in value.items():
            if key == "role" and isinstance(item, str):
                roles.append(item)
            if key in ("ansible.builtin.import_role", "ansible.builtin.include_role"):
                require(isinstance(item, dict), f"{key} must use structured arguments")
                if isinstance(item.get("name"), str):
                    roles.append(item["name"])
            roles.extend(referenced_roles(item))
        return roles

    require(
        "cilium" not in referenced_roles(node),
        "node convergence must not hide a Cilium deployment role import",
    )
    require(
        "cilium" not in referenced_roles(bootstrap),
        "kubeadm bootstrap must not hide a Cilium deployment role import",
    )


def validate_pins() -> None:
    defaults = load_yaml(CILIUM_ROLE / "defaults" / "main.yml")
    variables = load_yaml(CILIUM_ROLE / "vars" / "main.yml")
    require(
        defaults["cilium_chart_version"] == "{{ cilium_supported_chart_version }}",
        "public chart default must resolve from the integrity pin",
    )
    require(
        variables["cilium_supported_chart_version"] == "1.20.1",
        "chart pin drifted",
    )
    require(variables["cilium_helm_version"] == "4.2.4", "Helm pin drifted")
    expected_helm_artifacts = {
        "4.2.4": "c306b46f719b0a4da32d0f78ee21bf90ce8d602f15b22ab753f0674d1670a7f3"
    }
    expected_chart_artifacts = {
        "1.20.1": "sha256:906ce40d35daad838d12add8a5ba7033e767767f51799a93c7eace2cec9cdc05"
    }
    require(
        variables["cilium_helm_archive_sha256"]
        == expected_helm_artifacts.get(variables["cilium_helm_version"]),
        "Helm version and official archive SHA-256 were not reviewed together",
    )
    require(
        variables["cilium_chart_digest"]
        == expected_chart_artifacts.get(variables["cilium_supported_chart_version"]),
        "Cilium version and immutable OCI digest were not reviewed together",
    )
    require(
        "@{{ cilium_chart_digest }}"
        in (CILIUM_ROLE / "tasks" / "converge.yml").read_text(encoding="utf-8"),
        "every chart mutation must consume the OCI digest",
    )


def validate_state_machine() -> None:
    classify = (CILIUM_ROLE / "tasks" / "classify.yml").read_text(encoding="utf-8")
    converge = load_yaml(CILIUM_ROLE / "tasks" / "converge.yml")
    converge_text = (CILIUM_ROLE / "tasks" / "converge.yml").read_text(encoding="utf-8")

    for action in ("install", "upgrade", "validate"):
        require(
            f"cilium_release_action: {action}" in classify,
            f"state machine lacks {action!r}",
        )
    require(
        "cilium_existing_values == cilium_desired_values" in classify,
        "existing release values must be compared canonically",
    )
    require(
        "cilium_allow_upgrade" in classify,
        "upgrade must require explicit authorization",
    )

    commands = [
        arguments["argv"]
        for module, arguments in task_actions(converge)
        if module == "ansible.builtin.command"
    ]
    mutations = [
        command for command in commands if command[1] in ("install", "upgrade")
    ]
    require(len(mutations) == 2, "convergence must have only install and upgrade")
    require(mutations[0][1] == "install", "first mutation must be Helm install")
    require(mutations[1][1] == "upgrade", "second mutation must be Helm upgrade")
    for forbidden in ("uninstall", "delete", "reset", "kubeadm", "curl"):
        require(forbidden not in converge_text, f"forbidden mutation {forbidden!r}")
    require(
        "when: cilium_release_action == 'install'" in converge_text,
        "install must be state-gated",
    )
    require(
        "when: cilium_release_action == 'upgrade'" in converge_text,
        "upgrade must be state-gated",
    )


def validate_runtime_proofs() -> None:
    validation = (CILIUM_ROLE / "tasks" / "validate.yml").read_text(encoding="utf-8")
    manifest = (CILIUM_ROLE / "templates" / "validation-resources.yml.j2").read_text(
        encoding="utf-8"
    )
    required_proofs = (
        "daemonset/cilium",
        "deployment/cilium-operator",
        "cilium-dbg",
        "Ready=True",
        "deployment/coredns",
        "Pod and Service IPv4 addresses",
        "Pod-to-Pod connectivity",
        "Pod-to-ClusterIP connectivity",
        "cluster DNS resolution",
        "Pod Internet egress",
        "NetworkPolicy-denied path",
    )
    for proof in required_proofs:
        require(proof in validation, f"runtime proof missing {proof!r}")
    require("kind: NetworkPolicy" in manifest, "NetworkPolicy fixture missing")
    require("cilium-validation-access: allowed" in manifest, "allow label missing")
    require("cilium-validation-access: denied" in manifest, "deny label missing")
    require(
        "cilium_validation_namespace_created"
        in (CILIUM_ROLE / "tasks" / "cleanup.yml").read_text(encoding="utf-8"),
        "cleanup must be limited to lifecycle-owned resources",
    )


def validate_destructive_safeguards() -> None:
    values = (CILIUM_ROLE / "tasks" / "define_values.yml").read_text(encoding="utf-8")
    preflight = (CILIUM_ROLE / "tasks" / "preflight.yml").read_text(encoding="utf-8")
    for safeguard in (
        "waitForKubeProxy: true",
        "cleanBpfState: false",
        "cleanState: false",
        "uninstall: false",
    ):
        require(safeguard in values, f"destructive safeguard missing {safeguard!r}")
    require(
        "rollout" in preflight and "daemonset/kube-proxy" in preflight,
        "Cilium must wait for the retained kube-proxy before mutation",
    )


def main() -> None:
    validate_lifecycle_separation()
    validate_pins()
    validate_state_machine()
    validate_runtime_proofs()
    validate_destructive_safeguards()
    print(
        "Cilium lifecycle contract passed: separation, immutable pins, "
        "fail-closed state machine, idempotence, and runtime proofs intact"
    )


if __name__ == "__main__":
    main()
