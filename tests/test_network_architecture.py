#!/usr/bin/env python3

"""Validate the repository's non-operational Kubernetes network contract."""

from __future__ import annotations

import ast
import copy
import hashlib
import ipaddress
import json
import pathlib
import re
from collections import Counter
from collections.abc import Callable
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
CILIUM_HOST_ROLE_PATH = REPOSITORY_ROOT / "ansible" / "roles" / "cilium_host"
CILIUM_HOST_TASKS_PATH = CILIUM_HOST_ROLE_PATH / "tasks"
CILIUM_HOST_HANDLERS_PATH = CILIUM_HOST_ROLE_PATH / "handlers"

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
ALLOWED_CILIUM_HOST_TASK_KEYWORDS = {
    "always",
    "block",
    "changed_when",
    "failed_when",
    "loop",
    "loop_control",
    "name",
    "register",
    "rescue",
    "when",
}
CILIUM_HOST_TASK_BLOCK_KEYS = ("block", "rescue", "always")
EXPECTED_CILIUM_HOST_TASK_FILES = {"tasks/main.yml", "tasks/validate.yml"}
ANSIBLE_MODULE_KEY = re.compile(
    r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$"
)
APPROVED_MODINFO_ARGV_PREFIX = (
    "/sbin/modinfo",
    "--field",
    "filename",
)
# Hash the parsed probe so YAML/Python formatting may change without approving new behavior.
APPROVED_BPF_PROBE_AST_SHA256 = (
    "b1cfa9fb6bb94b362b5ae0df5a0aab0f12db993c3cd593b5e9ea3aade0496614"
)
APPROVED_CILIUM_HOST_COMMAND_KINDS = ["modinfo", "bpf_syscall_probe"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_validation_failure(
    name: str,
    validation: Callable[[], object],
    expected_message: str,
) -> None:
    try:
        validation()
    except AssertionError as error:
        require(
            expected_message in str(error),
            f"{name} failed for the wrong reason: {error}",
        )
        return
    raise AssertionError(f"{name} unexpectedly passed validation")


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


def validate_role_contract(contract: dict[str, Any]) -> None:
    inputs = contract["ansible_role_inputs"]
    require(
        set(inputs) == EXPECTED_ROLE_INPUTS,
        "the public-role input contract is incomplete",
    )
    require(
        all(specification["required"] is True for specification in inputs.values()),
        "every Cilium role input must be explicit",
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
    require(
        chart_version["default"] == "1.20.1",
        "the role contract must select the pinned Cilium chart version",
    )

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

    initial_cilium = contract["initial_cilium"]
    routing_mode = inputs["cilium_routing_mode"]
    tunnel_protocol = inputs["cilium_tunnel_protocol"]
    require(
        routing_mode["default"] == initial_cilium["routing_mode"],
        "role routing default must match the initial Cilium routing mode",
    )
    require(
        routing_mode["allowed"] == [initial_cilium["routing_mode"]],
        "role routing choices must contain only the initial Cilium routing mode",
    )
    require(
        tunnel_protocol["default"] == initial_cilium["tunnel_protocol"],
        "role tunnel default must match the initial Cilium tunnel protocol",
    )
    require(
        tunnel_protocol["allowed"] == [initial_cilium["tunnel_protocol"]],
        "role tunnel choices must contain only the initial Cilium tunnel protocol",
    )


def task_module_invocations(
    value: object,
    reference: str,
) -> list[tuple[str, str, object]]:
    require(isinstance(value, list), f"{reference} must contain an Ansible task list")
    invocations = []
    for index, task in enumerate(value):
        task_reference = f"{reference}[{index}]"
        require(isinstance(task, dict), f"{task_reference} must be a task mapping")

        module_keys = [
            key
            for key in task
            if isinstance(key, str) and ANSIBLE_MODULE_KEY.fullmatch(key) is not None
        ]
        unsupported_keys = [
            key
            for key in task
            if key not in ALLOWED_CILIUM_HOST_TASK_KEYWORDS and key not in module_keys
        ]
        require(
            not unsupported_keys,
            f"{task_reference} has unsupported task or action keys: "
            f"{unsupported_keys!r}",
        )

        block_keys = [key for key in CILIUM_HOST_TASK_BLOCK_KEYS if key in task]
        if block_keys:
            require(
                "block" in block_keys,
                f"{task_reference} may use rescue or always only with block",
            )
            require(
                not module_keys,
                f"{task_reference} must not combine a block with a module action",
            )
            for block_key in block_keys:
                invocations.extend(
                    task_module_invocations(
                        task[block_key], f"{task_reference}.{block_key}"
                    )
                )
            continue

        require(
            len(module_keys) == 1,
            f"{task_reference} must use exactly one fully qualified module action",
        )
        module_name = module_keys[0]
        invocations.append(
            (f"{task_reference}.{module_name}", module_name, task[module_name])
        )
    return invocations


def cilium_host_command_kind(command: object, reference: str) -> str:
    require(
        isinstance(command, dict),
        f"{reference} must use structured command module arguments",
    )
    require(
        set(command) == {"argv"},
        f"{reference} may set only argv, got {sorted(command)}",
    )
    argv = command["argv"]
    require(
        isinstance(argv, list) and all(isinstance(value, str) for value in argv),
        f"{reference}.argv must be a string list",
    )

    if (
        len(argv) == 4
        and tuple(argv[:3]) == APPROVED_MODINFO_ARGV_PREFIX
        and re.sub(r"\s+", "", argv[3]) == "{{item.module}}"
    ):
        return "modinfo"

    if (
        len(argv) == 3
        and re.sub(r"\s+", "", argv[0]) == "{{ansible_facts.python.executable}}"
        and argv[1] == "-c"
    ):
        try:
            probe_ast = ast.dump(
                ast.parse(argv[2]), annotate_fields=True, include_attributes=False
            )
        except SyntaxError as error:
            raise AssertionError(
                f"{reference} BPF probe body is not valid Python"
            ) from error
        probe_sha256 = hashlib.sha256(probe_ast.encode("utf-8")).hexdigest()
        require(
            probe_sha256 == APPROVED_BPF_PROBE_AST_SHA256,
            f"{reference} BPF probe body is not the explicitly approved probe",
        )
        return "bpf_syscall_probe"

    raise AssertionError(f"{reference} invokes an unapproved command: {argv!r}")


def load_cilium_host_task_documents() -> dict[str, object]:
    paths = sorted(
        path
        for directory in (CILIUM_HOST_TASKS_PATH, CILIUM_HOST_HANDLERS_PATH)
        if directory.exists()
        for path in directory.rglob("*")
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    )
    return {
        path.relative_to(CILIUM_HOST_ROLE_PATH).as_posix(): yaml.safe_load(
            path.read_text(encoding="utf-8")
        )
        for path in paths
    }


def validate_cilium_host_task_documents(documents: dict[str, object]) -> None:
    require(
        set(documents) == EXPECTED_CILIUM_HOST_TASK_FILES,
        "cilium_host executable files must be exactly tasks/main.yml and "
        "tasks/validate.yml, got "
        f"{sorted(documents)}",
    )

    main_tasks = documents["tasks/main.yml"]
    require(
        isinstance(main_tasks, list)
        and len(main_tasks) == 1
        and isinstance(main_tasks[0], dict),
        "cilium_host main must have one boundary task",
    )
    require(
        main_tasks[0].get("ansible.builtin.import_tasks") == "validate.yml",
        "cilium_host main must import prerequisite validation only",
    )

    invocations = []
    for path, document in sorted(documents.items()):
        invocations.extend(task_module_invocations(document, path))

    unexpected_modules = {
        module_name
        for _, module_name, _ in invocations
        if module_name not in ALLOWED_CILIUM_HOST_MODULES
    }
    require(
        not unexpected_modules,
        f"cilium_host has operational modules: {sorted(unexpected_modules)}",
    )

    import_targets = [
        arguments
        for _, module_name, arguments in invocations
        if module_name == "ansible.builtin.import_tasks"
    ]
    require(
        import_targets == ["validate.yml"],
        "cilium_host must import only validate.yml from main.yml, got "
        f"{import_targets!r}",
    )

    command_kinds = [
        cilium_host_command_kind(arguments, invocation_reference)
        for invocation_reference, module_name, arguments in invocations
        if module_name == "ansible.builtin.command"
    ]
    require(
        Counter(command_kinds) == Counter(APPROVED_CILIUM_HOST_COMMAND_KINDS),
        "cilium_host command tasks must be exactly the approved modinfo and BPF "
        f"probes, got {command_kinds}",
    )


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

    validate_cilium_host_task_documents(load_cilium_host_task_documents())

    require(
        (REPOSITORY_ROOT / "ansible" / "roles" / "cilium").is_dir(),
        "the dedicated Cilium lifecycle role is missing",
    )
    require(
        (REPOSITORY_ROOT / "ansible" / "playbooks" / "cilium.yml").is_file(),
        "the dedicated Cilium lifecycle playbook is missing",
    )

    for playbook_name in ("node.yml", "bootstrap.yml"):
        playbook = (
            REPOSITORY_ROOT / "ansible" / "playbooks" / playbook_name
        ).read_text(encoding="utf-8")
        require(
            re.search(r"(?m)^\s*- role: cilium\s*$", playbook) is None,
            f"{playbook_name} must not invoke the Cilium deployment role",
        )


def validate_regression_guards(contract: dict[str, Any]) -> None:
    task_documents = load_cilium_host_task_documents()
    boundary_cases = (
        (
            "arbitrary cilium_host command",
            {
                "name": "Nested operational block fixture",
                "block": [
                    {
                        "name": "Apply an unapproved manifest",
                        "ansible.builtin.command": {
                            "argv": [
                                "/usr/bin/kubectl",
                                "apply",
                                "--filename",
                                "cilium.yml",
                            ]
                        },
                    }
                ],
            },
            "invokes an unapproved command",
        ),
        (
            "action syntax",
            {
                "name": "Hide an operational command behind action",
                "action": "ansible.builtin.command /usr/bin/helm upgrade cilium",
            },
            "unsupported task or action keys",
        ),
        (
            "local_action syntax",
            {
                "name": "Hide an operational command behind local_action",
                "local_action": "ansible.builtin.command /usr/bin/cilium install",
            },
            "unsupported task or action keys",
        ),
        (
            "unqualified command syntax",
            {
                "name": "Hide an operational command behind a short module name",
                "command": "/usr/bin/cilium upgrade",
            },
            "unsupported task or action keys",
        ),
        (
            "sibling command arguments",
            {
                "name": "Extend an approved command outside its module mapping",
                "ansible.builtin.command": {
                    "argv": [
                        "/sbin/modinfo",
                        "--field",
                        "filename",
                        "{{ item.module }}",
                    ]
                },
                "args": {"chdir": "/tmp"},
            },
            "unsupported task or action keys",
        ),
    )
    for name, task, expected_message in boundary_cases:
        drifted_documents = copy.deepcopy(task_documents)
        drifted_documents["tasks/validate.yml"].append(task)
        require_validation_failure(
            f"{name} regression",
            lambda fixture=drifted_documents: validate_cilium_host_task_documents(
                fixture
            ),
            expected_message,
        )

    require_validation_failure(
        "modified Python probe regression",
        lambda: cilium_host_command_kind(
            {
                "argv": [
                    "{{ ansible_facts.python.executable }}",
                    "-c",
                    "import subprocess; subprocess.run(['kubectl', 'apply'])",
                ]
            },
            "unapproved Python probe fixture",
        ),
        "BPF probe body is not the explicitly approved probe",
    )

    imported_documents = copy.deepcopy(task_documents)
    imported_documents["tasks/validate.yml"].append(
        {
            "name": "Import an unapproved task file",
            "ansible.builtin.import_tasks": "install.yaml",
        }
    )
    require_validation_failure(
        "additional task import regression",
        lambda: validate_cilium_host_task_documents(imported_documents),
        "must import only validate.yml from main.yml",
    )

    for extra_path in ("tasks/nested/install.yaml", "handlers/main.yml"):
        extra_file_documents = copy.deepcopy(task_documents)
        extra_file_documents[extra_path] = [
            {
                "name": "Install Cilium from an unapproved executable file",
                "ansible.builtin.command": {"argv": ["/usr/bin/cilium", "install"]},
            }
        ]
        require_validation_failure(
            f"additional executable file {extra_path} regression",
            lambda fixture=extra_file_documents: validate_cilium_host_task_documents(
                fixture
            ),
            "executable files must be exactly tasks/main.yml and tasks/validate.yml",
        )

    drift_cases = (
        ("routing default", "cilium_routing_mode", "default", "native"),
        (
            "routing allowed values",
            "cilium_routing_mode",
            "allowed",
            ["tunnel", "native"],
        ),
        ("tunnel default", "cilium_tunnel_protocol", "default", "geneve"),
        (
            "tunnel allowed values",
            "cilium_tunnel_protocol",
            "allowed",
            ["vxlan", "geneve"],
        ),
    )
    for name, input_name, field, value in drift_cases:
        drifted_contract = copy.deepcopy(contract)
        drifted_contract["ansible_role_inputs"][input_name][field] = value
        require_validation_failure(
            f"{name} regression",
            lambda fixture=drifted_contract: validate_role_contract(fixture),
            "must match" if field == "default" else "must contain only",
        )


def main() -> None:
    contract = load_json_object(CONTRACT_PATH)
    require(contract["contract_version"] == 2, "unsupported network contract version")
    require(
        contract["scope"] == "architecture-and-cilium-lifecycle",
        "contract scope must cover the implemented Cilium lifecycle",
    )
    validate_address_plan(contract)
    validate_initial_cilium_contract(contract)
    validate_role_contract(contract)
    validate_repository_boundaries(contract)
    validate_regression_guards(contract)
    print(
        "Network architecture contract passed: IPv4 CIDRs disjoint, "
        "Cilium deferred-feature and lifecycle boundaries intact"
    )


if __name__ == "__main__":
    main()
