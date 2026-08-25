#!/usr/bin/env python3

"""Validate the repository's non-operational Kubernetes network contract."""

from __future__ import annotations

import ipaddress
import json
import pathlib
import re
from typing import Any

import yaml

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPOSITORY_ROOT / "docs" / "network-contract.json"
KUBEADM_DEFAULTS_PATH = (
    REPOSITORY_ROOT
    / "ansible"
    / "roles"
    / "kubernetes_bootstrap"
    / "defaults"
    / "main.yml"
)
KUBEADM_TEMPLATE_PATH = (
    REPOSITORY_ROOT
    / "ansible"
    / "roles"
    / "kubernetes_bootstrap"
    / "templates"
    / "kubeadm-init.yaml.j2"
)
KUBEADM_VALIDATION_PATH = (
    REPOSITORY_ROOT
    / "ansible"
    / "roles"
    / "kubernetes_bootstrap"
    / "tasks"
    / "validate.yml"
)
CILIUM_HOST_TASKS_PATH = REPOSITORY_ROOT / "ansible" / "roles" / "cilium_host" / "tasks"

EXPECTED_DEFERRED_FEATURES = {
    "hubble",
    "hubble_relay",
    "wireguard_transparent_encryption",
    "gateway_api",
    "cilium_ingress",
    "bgp_control_plane",
    "clustermesh",
    "kube_proxy_replacement",
    "big_tcp",
}
EXPECTED_ROLE_INPUTS = {
    "cilium_cluster_name",
    "cilium_cluster_id",
    "cilium_cluster_pool_ipv4_cidr",
    "cilium_cluster_pool_ipv4_mask_size",
    "cilium_chart_version",
    "cilium_routing_mode",
    "cilium_tunnel_protocol",
}
ALLOWED_CILIUM_HOST_MODULES = {
    "ansible.builtin.assert",
    "ansible.builtin.command",
    "ansible.builtin.import_tasks",
    "ansible.builtin.set_fact",
    "ansible.builtin.slurp",
    "ansible.builtin.stat",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json_object(path: pathlib.Path) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            require(key not in result, f"{path} contains duplicate key {key!r}")
            result[key] = value
        return result

    document = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=unique_object
    )
    require(isinstance(document, dict), f"{path} must contain a JSON object")
    return document


def ipv4_network(value: object, reference: str) -> ipaddress.IPv4Network:
    require(isinstance(value, str), f"{reference} must be a string")
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise AssertionError(
            f"{reference} must be a canonical CIDR: {error}"
        ) from error
    require(network.version == 4, f"{reference} must be IPv4")
    return network


def require_pairwise_disjoint(
    networks: list[tuple[str, ipaddress.IPv4Network]],
) -> None:
    for index, (left_name, left) in enumerate(networks):
        for right_name, right in networks[index + 1 :]:
            require(
                not left.overlaps(right),
                f"{left_name} {left} overlaps {right_name} {right}",
            )


def validate_address_plan(contract: dict[str, Any]) -> None:
    address_plan = contract["address_plan"]
    require(address_plan["ip_family"] == "ipv4", "networking must remain IPv4-only")

    services = ipv4_network(
        address_plan["kubernetes_service_cidr"],
        "address_plan.kubernetes_service_cidr",
    )
    current_pods = ipv4_network(
        address_plan["current_cluster_pod_cidr"],
        "address_plan.current_cluster_pod_cidr",
    )
    private_nodes = ipv4_network(
        address_plan["future_hetzner_private_node_network"],
        "address_plan.future_hetzner_private_node_network",
    )
    pod_reservation = ipv4_network(
        address_plan["cluster_pod_cidr_reservation"],
        "address_plan.cluster_pod_cidr_reservation",
    )
    future_pods = [
        (
            f"address_plan.future_cluster_pod_cidrs[{index}]",
            ipv4_network(value, f"address_plan.future_cluster_pod_cidrs[{index}]"),
        )
        for index, value in enumerate(address_plan["future_cluster_pod_cidrs"])
    ]

    require(str(services) == "10.96.0.0/12", "Service CIDR must remain 10.96.0.0/12")
    require(str(current_pods) == "10.200.0.0/16", "current Pod pool must be /16")
    require(str(private_nodes) == "10.10.0.0/16", "private node reservation drifted")
    require(str(pod_reservation) == "10.200.0.0/13", "Pod reservation drifted")
    require(len(future_pods) == 7, "seven future cluster /16 pools must be reserved")

    per_node_prefix = address_plan["per_node_pod_cidr_prefix"]
    require(per_node_prefix == 24, "per-node Pod CIDRs must remain /24")
    require(
        current_pods.prefixlen < per_node_prefix <= current_pods.max_prefixlen,
        "per-node prefix must fit inside the current cluster Pod pool",
    )

    cluster_pools = [("current cluster Pod CIDR", current_pods), *future_pods]
    require_pairwise_disjoint(cluster_pools)
    for name, pool in cluster_pools:
        require(
            pool.prefixlen == 16,
            f"{name} must reserve one /16 per cluster, got {pool}",
        )
        require(
            pool.subnet_of(pod_reservation),
            f"{name} {pool} must fit within {pod_reservation}",
        )
        require(
            not pool.overlaps(services),
            f"{name} {pool} overlaps the Service CIDR {services}",
        )
        require(
            not pool.overlaps(private_nodes),
            f"{name} {pool} overlaps the private node network {private_nodes}",
        )
    require_pairwise_disjoint(
        [
            ("Kubernetes Service CIDR", services),
            ("future Hetzner private node network", private_nodes),
            ("cluster Pod CIDR reservation", pod_reservation),
        ]
    )


def validate_initial_cilium_contract(contract: dict[str, Any]) -> None:
    cilium = contract["initial_cilium"]
    require(cilium["ipv4_enabled"] is True, "Cilium IPv4 must be enabled")
    require(cilium["ipv6_enabled"] is False, "Cilium IPv6 must be disabled")
    require(cilium["ipam_mode"] == "cluster-pool", "IPAM must remain cluster-pool")
    require(cilium["routing_mode"] == "tunnel", "routing must start in tunnel mode")
    require(cilium["tunnel_protocol"] == "vxlan", "initial tunnel must be VXLAN")
    require(cilium["ipv4_masquerade"] is True, "IPv4 masquerading must be enabled")
    require(cilium["bpf_masquerade"] is False, "BPF masquerading must be deferred")
    require(cilium["operator_replicas"] == 1, "single-node operator replicas must be 1")

    deferred = cilium["deferred_features"]
    require(
        set(deferred) == EXPECTED_DEFERRED_FEATURES,
        "the deferred feature set is incomplete or contains an unknown feature",
    )
    require(
        all(value is False for value in deferred.values()),
        "all deferred Cilium features must remain disabled",
    )


def validate_future_role_contract(contract: dict[str, Any]) -> None:
    inputs = contract["future_ansible_role_inputs"]
    require(
        set(inputs) == EXPECTED_ROLE_INPUTS,
        "the future public-role input contract is incomplete",
    )
    require(
        all(specification["required"] is True for specification in inputs.values()),
        "every future Cilium role input must be explicit",
    )

    name = inputs["cilium_cluster_name"]
    cluster_id = inputs["cilium_cluster_id"]
    chart_version = inputs["cilium_chart_version"]
    require(
        name["default"] is None, "reusable code must not default production identity"
    )
    require(cluster_id["default"] is None, "cluster ID must come from live inventory")
    require(
        name["source"] == cluster_id["source"] == "private_live_inventory",
        "permanent cluster identity must come from private live inventory",
    )
    require(
        name["lifecycle"] == cluster_id["lifecycle"] == "permanent_once_deployed",
        "Cilium cluster identity must remain permanent once deployed",
    )
    require(name["maximum_length"] == 32, "Cilium cluster names are at most 32 chars")
    require(
        re.fullmatch(name["pattern"], "future-cluster") is not None,
        "cluster-name pattern must accept a topology-neutral example",
    )
    for invalid_name in ("Future-cluster", "-future", "future-", "a" * 33):
        require(
            re.fullmatch(name["pattern"], invalid_name) is None,
            f"cluster-name pattern must reject {invalid_name!r}",
        )
    require(
        set(name["forbidden_sources"]) == {"node_hostname", "current_topology"},
        "cluster names must not be derived from a node or current topology",
    )
    require(
        name["forbidden_substrings"] == ["single-node"],
        "cluster names must not encode the current single-node topology",
    )
    require(
        (cluster_id["minimum"], cluster_id["maximum"]) == (1, 255),
        "Cilium cluster ID must be constrained to 1..255",
    )
    require(chart_version["default"] is None, "this PR must not select a chart version")

    address_plan = contract["address_plan"]
    require(
        inputs["cilium_cluster_pool_ipv4_cidr"]["default"]
        == address_plan["current_cluster_pod_cidr"],
        "role Pod pool default must match the current address plan",
    )
    require(
        inputs["cilium_cluster_pool_ipv4_mask_size"]["default"]
        == address_plan["per_node_pod_cidr_prefix"],
        "role node mask default must match the address plan",
    )


def task_module_names(value: object) -> set[str]:
    if isinstance(value, dict):
        names = {
            key
            for key in value
            if isinstance(key, str)
            and key.startswith(("ansible.builtin.", "kubernetes.core."))
        }
        for nested in value.values():
            names.update(task_module_names(nested))
        return names
    if isinstance(value, list):
        names: set[str] = set()
        for nested in value:
            names.update(task_module_names(nested))
        return names
    return set()


def validate_repository_boundaries(contract: dict[str, Any]) -> None:
    defaults = yaml.safe_load(KUBEADM_DEFAULTS_PATH.read_text(encoding="utf-8"))
    template = KUBEADM_TEMPLATE_PATH.read_text(encoding="utf-8")
    live_validation = KUBEADM_VALIDATION_PATH.read_text(encoding="utf-8")
    require(
        defaults["kubernetes_bootstrap_service_subnet"]
        == contract["address_plan"]["kubernetes_service_cidr"],
        "kubeadm Service CIDR must match the network contract",
    )
    require(
        re.search(r"(?m)^\s*podSubnet:", template) is None,
        "kubeadm must not configure a Pod subnet",
    )
    require(
        "serviceSubnet: {{ kubernetes_bootstrap_service_subnet | to_json }}"
        in template,
        "kubeadm template must render the validated Service CIDR input",
    )
    require(
        "kubernetes_bootstrap_kube_proxy.stdout == 'daemonset.apps/kube-proxy'"
        in live_validation,
        "bootstrap validation must require kube-proxy during the first lifecycle",
    )

    cilium_host_main = yaml.safe_load(
        (CILIUM_HOST_TASKS_PATH / "main.yml").read_text(encoding="utf-8")
    )
    require(len(cilium_host_main) == 1, "cilium_host main must have one boundary task")
    require(
        cilium_host_main[0].get("ansible.builtin.import_tasks") == "validate.yml",
        "cilium_host must import prerequisite validation only",
    )
    for path in sorted(CILIUM_HOST_TASKS_PATH.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        unexpected = task_module_names(document) - ALLOWED_CILIUM_HOST_MODULES
        require(
            not unexpected,
            f"{path.relative_to(REPOSITORY_ROOT)} has operational modules: "
            f"{sorted(unexpected)}",
        )
        lowered = path.read_text(encoding="utf-8").casefold()
        require("cilium install" not in lowered, f"{path} must not install Cilium")
        require("helm" not in lowered, f"{path} must not invoke Helm")

    require(
        not (REPOSITORY_ROOT / "ansible" / "roles" / "cilium").exists(),
        "this architecture-only change must not add a Cilium deployment role",
    )
    require(
        not (REPOSITORY_ROOT / "ansible" / "playbooks" / "cilium.yml").exists(),
        "this architecture-only change must not add a Cilium playbook",
    )


def main() -> None:
    contract = load_json_object(CONTRACT_PATH)
    require(contract["contract_version"] == 1, "unsupported network contract version")
    require(contract["scope"] == "architecture-only", "contract scope must be inert")
    validate_address_plan(contract)
    validate_initial_cilium_contract(contract)
    validate_future_role_contract(contract)
    validate_repository_boundaries(contract)
    print(
        "Network architecture contract passed: IPv4 CIDRs disjoint, "
        "Cilium features deferred, deployment boundary intact"
    )


if __name__ == "__main__":
    main()
