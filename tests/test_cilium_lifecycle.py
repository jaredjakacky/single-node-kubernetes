#!/usr/bin/env python3

"""Validate the Cilium lifecycle's static safety and idempotence boundaries."""

from __future__ import annotations

import pathlib
import re
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


def task_actions(value: Any) -> list[tuple[str, dict[str, Any]]]:
    actions = []
    if isinstance(value, list):
        for item in value:
            actions.extend(task_actions(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key.startswith("ansible.builtin."):
                require(isinstance(item, dict), f"{key} must use structured arguments")
                actions.append((key, item))
            else:
                actions.extend(task_actions(item))
    return actions


def task_command_argvs(value: Any, path: pathlib.Path) -> list[list[str]]:
    commands = []
    if isinstance(value, list):
        for item in value:
            commands.extend(task_command_argvs(item, path))
    elif isinstance(value, dict):
        arguments = value.get("ansible.builtin.command")
        if arguments is not None:
            require(
                isinstance(arguments, dict),
                f"{path}: command must use structured arguments",
            )
            argv = arguments.get("argv")
            if isinstance(argv, list):
                commands.append(argv)
            else:
                require(
                    argv == "{{ item }}" and isinstance(value.get("loop"), list),
                    f"{path}: dynamic command argv must resolve from a literal loop",
                )
                commands.extend(value["loop"])
        for block in ("block", "rescue", "always"):
            if block in value:
                commands.extend(task_command_argvs(value[block], path))
    return commands


def role_helm_commands() -> list[tuple[pathlib.Path, list[str]]]:
    commands = []
    for path in sorted((CILIUM_ROLE / "tasks").glob("*.yml")):
        tasks = load_yaml(path)
        task_text = path.read_text(encoding="utf-8")
        for module in ("ansible.builtin.raw", "ansible.builtin.shell"):
            require(
                module not in task_text,
                f"{path}: Cilium commands must not be hidden in {module}",
            )
        for argv in task_command_argvs(tasks, path):
            require(argv, f"{path}: command argv must not be empty")
            executable = str(argv[0])
            if "helm" in executable.lower():
                require(
                    executable == "{{ cilium_helm_binary }}",
                    f"{path}: Helm must use the pinned cilium_helm_binary",
                )
                require(
                    all(isinstance(argument, str) for argument in argv),
                    f"{path}: Helm argv must contain only strings",
                )
                commands.append((path, argv))
    return commands


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


def validate_cni_package_baseline() -> None:
    variables = load_yaml(CILIUM_ROLE / "vars" / "main.yml")
    normalize_path = CILIUM_ROLE / "tasks" / "normalize_cni_state.yml"
    normalize = load_yaml(normalize_path)
    normalize_text = normalize_path.read_text(encoding="utf-8")
    detect_text = (CILIUM_ROLE / "tasks" / "detect.yml").read_text(encoding="utf-8")
    classify_text = (CILIUM_ROLE / "tasks" / "classify.yml").read_text(encoding="utf-8")
    fixture = (ANSIBLE_ROOT / "tests" / "cilium-cni-baseline.yml").read_text(
        encoding="utf-8"
    )
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "ansible-ci.yaml"
    ).read_text(encoding="utf-8")

    sentinel = "/etc/cni/net.d/.kubernetes-cni-keep"
    cilium_config = "/etc/cni/net.d/05-cilium.conflist"
    require(
        variables["cilium_kubernetes_cni_keep_path"] == sentinel,
        "the Kubernetes CNI sentinel must remain an exact path constant",
    )
    require(
        variables["cilium_kubernetes_cni_package"] == "kubernetes-cni",
        "the sentinel must remain tied to its upstream Debian package",
    )
    require(
        variables["cilium_dpkg_query_binary"] == "/usr/bin/dpkg-query",
        "sentinel ownership must use Debian's installed-package database",
    )
    require(
        variables["cilium_expected_cni_config_path"] == cilium_config,
        "the managed Cilium CNI path drifted",
    )

    task_names = [task["name"] for task in normalize]
    validation_index = task_names.index(
        "Validate the optional Kubernetes CNI package sentinel"
    )
    normalization_index = task_names.index(
        "Normalize raw, package-baseline, and meaningful CNI state"
    )
    require(
        validation_index < normalization_index,
        "the sentinel must be positively validated before it is excluded",
    )
    require(
        "ansible.builtin.stat" in normalize[0]
        and normalize[0]["ansible.builtin.stat"]["path"]
        == "{{ cilium_kubernetes_cni_keep_path }}",
        "sentinel metadata must be read from the exact expected path",
    )
    ownership_task = next(
        task
        for task in normalize
        if task["name"] == "Read package ownership for the Kubernetes CNI sentinel"
    )
    require(
        ownership_task["ansible.builtin.command"]["argv"]
        == [
            "{{ cilium_dpkg_query_binary }}",
            "--search",
            "{{ cilium_kubernetes_cni_keep_path }}",
        ],
        "dpkg ownership must query the exact sentinel path structurally",
    )
    for proof in (
        ".stat.isreg",
        ".stat.islnk",
        ".stat.size",
        ".stat.uid",
        ".stat.gid",
        ".stat.mode",
        "cilium_kubernetes_cni_keep_ownership.rc",
        "cilium_kubernetes_cni_keep_ownership.stdout_lines",
        "cilium_kubernetes_cni_keep_owner_pattern",
    ):
        require(proof in normalize_text, f"sentinel proof missing {proof!r}")
    for forbidden in (
        "basename",
        "*.kubernetes-cni-keep",
        "*.keep",
        "fileglob",
        "ansible.builtin.shell",
        "ansible.builtin.raw",
        "ansible.builtin.file",
        "ansible.builtin.copy",
    ):
        require(
            forbidden not in normalize_text,
            f"CNI baseline introduced broad or mutating logic {forbidden!r}",
        )

    owner_pattern = re.compile(
        rf"^kubernetes-cni(?::[a-z0-9][a-z0-9-]*)?: {re.escape(sentinel)}$"
    )
    require(
        owner_pattern.fullmatch(f"kubernetes-cni: {sentinel}") is not None,
        "unqualified Debian package ownership must be accepted",
    )
    require(
        owner_pattern.fullmatch(f"kubernetes-cni:amd64: {sentinel}") is not None,
        "architecture-qualified Debian package ownership must be accepted",
    )
    for invalid_owner in (
        f"not-kubernetes-cni: {sentinel}",
        "kubernetes-cni: /etc/cni/net.d/other",
        f"kubernetes-cni:amd64: {sentinel}\nother: {sentinel}",
    ):
        require(
            owner_pattern.fullmatch(invalid_owner) is None,
            f"invalid package ownership was accepted: {invalid_owner!r}",
        )

    require(
        "ansible.builtin.import_tasks: normalize_cni_state.yml" in detect_text,
        "live detection must normalize and validate the package baseline",
    )
    require(
        "selectattr('path', 'equalto', cilium_expected_cni_config_path)" in detect_text,
        "the managed Cilium config must be selected by exact path",
    )
    require(
        "cilium_cni_entries.files[0]" not in detect_text,
        "managed CNI validation must never rely on find ordering",
    )
    require(
        "cilium_managed_cni_entries[0]" in detect_text,
        "managed metadata must validate the exact selected Cilium entry",
    )
    require(
        "cilium_observed_meaningful_cni_paths" in classify_text,
        "classification must operate on meaningful CNI state",
    )
    require(
        "cilium_observed_cni_paths | length == 0" not in classify_text,
        "first install must not require a literally empty raw directory",
    )
    for runtime_case in (
        "exact package sentinel",
        "sentinel plus unexpected ordinary file",
        "nonzero sentinel",
        "group-writable sentinel",
        "non-root-owned sentinel",
        "sentinel symlink",
        "unowned sentinel-shaped file",
    ):
        require(
            runtime_case in fixture, f"runtime CNI fixture missing {runtime_case!r}"
        )
    require(
        "sudo /usr/bin/dpkg --install" in workflow
        and "tests/cilium-cni-baseline.yml" in workflow,
        "CI must exercise the role against the real upstream Debian package",
    )
    require(
        'package_version="1.9.1-1.1"' in workflow
        and "4cd72d8cef4499d3dc410874287b40e" in workflow
        and "8b4241e0772938c5820cbee37986c1d93" in workflow,
        "the real-package fixture must remain checksum and version pinned",
    )

    readme = (ANSIBLE_ROOT / "README.md").read_text(encoding="utf-8")
    for extension in ("`.conf`", "`.conflist`", "`.json`"):
        require(extension in readme, f"Cilium cleanup documentation lacks {extension}")


def validate_helm_lifecycle_contract() -> None:
    commands = role_helm_commands()
    operations = [argv[1] for _, argv in commands]
    require(
        set(operations)
        == {
            "get",
            "install",
            "list",
            "show",
            "status",
            "template",
            "upgrade",
            "version",
        },
        "Cilium Helm operations must remain inside the reviewed CLI surface",
    )

    detect_path = CILIUM_ROLE / "tasks" / "detect.yml"
    detect_lists = [
        argv for path, argv in commands if path == detect_path and argv[1] == "list"
    ]
    require(len(detect_lists) == 1, "state detection must use exactly one Helm list")
    require(
        detect_lists[0]
        == [
            "{{ cilium_helm_binary }}",
            "list",
            "--namespace",
            "{{ cilium_release_namespace }}",
            "--filter",
            "^cilium$",
            "--output",
            "json",
            "--kubeconfig",
            "{{ cilium_kubeconfig_path }}",
        ],
        "state detection must use Helm 4's all-status default with exact "
        "scope and JSON",
    )
    status_filters = {
        "--deployed",
        "--failed",
        "--pending",
        "--superseded",
        "--uninstalled",
        "--uninstalling",
    }
    for _, argv in commands:
        if argv[1] == "list":
            require("--all" not in argv, "Helm 4 list does not support --all")
            require(
                status_filters.isdisjoint(argv),
                "Helm list must retain the Helm 4 default that includes every status",
            )

    mutations = [
        (path, argv) for path, argv in commands if argv[1] in ("install", "upgrade")
    ]
    require(
        [(path.name, argv[1]) for path, argv in mutations]
        == [("converge.yml", "install"), ("converge.yml", "upgrade")],
        "install and upgrade in converge.yml must remain the only Helm mutations",
    )
    for _, argv in mutations:
        require(
            "--rollback-on-failure" in argv,
            f"Helm {argv[1]} must roll back on failure",
        )
        require("--wait" in argv, f"Helm {argv[1]} must wait deterministically")
        require(
            "--wait-for-jobs" in argv,
            f"Helm {argv[1]} must wait for jobs",
        )
        require("--timeout" in argv, f"Helm {argv[1]} must retain its timeout")
    require(
        "--history-max" in mutations[1][1] and "10" in mutations[1][1],
        "Helm upgrade must retain the bounded history limit",
    )
    require(
        all("--atomic" not in argv for _, argv in commands),
        "deprecated Helm --atomic automation must not return",
    )


def validate_install_path_assertion_contract() -> None:
    converge = (CILIUM_ROLE / "tasks" / "converge.yml").read_text(encoding="utf-8")
    metadata_validation = load_yaml(
        CILIUM_ROLE / "tasks" / "validate_chart_metadata.yml"
    )
    workload_validation = load_yaml(
        CILIUM_ROLE / "tasks" / "validate_rendered_workloads.yml"
    )
    runtime_fixture = (ANSIBLE_ROOT / "tests" / "cilium-install-path.yml").read_text(
        encoding="utf-8"
    )
    metadata_case = (
        ANSIBLE_ROOT / "tests" / "tasks" / "cilium-chart-metadata-case.yml"
    ).read_text(encoding="utf-8")
    workload_case = (
        ANSIBLE_ROOT / "tests" / "tasks" / "cilium-rendered-workloads-case.yml"
    ).read_text(encoding="utf-8")
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "ansible-ci.yaml"
    ).read_text(encoding="utf-8")

    require(
        converge.index("show")
        < converge.index("validate_chart_metadata.yml")
        < converge.index("template")
        < converge.index("validate_rendered_workloads.yml")
        < converge.index("Install the pinned Cilium chart"),
        "chart metadata and rendered workloads must be validated before install",
    )
    for validation in (metadata_validation, workload_validation):
        require(
            validation[0].get("when")
            == "cilium_release_action in ['install', 'upgrade']",
            "both shared production assertions must remain install/upgrade gated",
        )
    for case, shared_task in (
        (metadata_case, "validate_chart_metadata.yml"),
        (workload_case, "validate_rendered_workloads.yml"),
    ):
        require(
            f"tasks_from: {shared_task}" in case
            and "cilium_release_action: install" in case,
            "runtime regressions must exercise each shared production assertion "
            "through its install action gate",
        )
        require(
            "Conditional expressions must be strings" in case
            and "Conditionals must have a boolean result" in case,
            "runtime regressions must distinguish assertions from parser/type errors",
        )
    for required_case in (
        "exact pinned metadata",
        "quoted pinned version metadata",
        "wrong chart name",
        "wrong chart version",
        "wrong chart application version",
        "mismatched chart metadata quotes",
        "exact agent and operator workloads",
        "missing Cilium agent workload",
        "wrong Cilium agent workload name",
        "missing Cilium operator workload",
        "wrong Cilium operator workload name",
    ):
        require(
            required_case in runtime_fixture, f"runtime case missing {required_case!r}"
        )
    require(
        "python tests/test_ansible_conditionals.py" in workflow,
        "CI must scan every Ansible conditional shape",
    )
    require(
        "ansible-playbook tests/cilium-install-path.yml" in workflow,
        "CI must execute the non-mutating install-path assertions",
    )
    require(
        "ALLOW_BROKEN_CONDITIONALS\\(default\\) = False" in workflow
        and "ANSIBLE_ALLOW_BROKEN_CONDITIONALS" not in workflow,
        "CI must keep ansible-core broken-conditional compatibility disabled",
    )


def validate_runtime_proofs() -> None:
    validation = (CILIUM_ROLE / "tasks" / "validate.yml").read_text(encoding="utf-8")
    topology = (CILIUM_ROLE / "tasks" / "validate_topology.yml").read_text(
        encoding="utf-8"
    )
    validation_contract = validation + topology
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
        require(proof in validation_contract, f"runtime proof missing {proof!r}")
    require("kind: NetworkPolicy" in manifest, "NetworkPolicy fixture missing")
    require("cilium-validation-access: allowed" in manifest, "allow label missing")
    require("cilium-validation-access: denied" in manifest, "deny label missing")
    require(
        "cilium_validation_namespace_created"
        in (CILIUM_ROLE / "tasks" / "cleanup.yml").read_text(encoding="utf-8"),
        "cleanup must be limited to lifecycle-owned resources",
    )


def validate_topology_policy_separation() -> None:
    lifecycle = (CILIUM_ROLE / "tasks" / "lifecycle.yml").read_text(encoding="utf-8")
    preflight = (CILIUM_ROLE / "tasks" / "preflight.yml").read_text(encoding="utf-8")
    policy = (CILIUM_ROLE / "tasks" / "enforce_phase_one_policy.yml").read_text(
        encoding="utf-8"
    )
    validation = (CILIUM_ROLE / "tasks" / "validate.yml").read_text(encoding="utf-8")
    topology = (CILIUM_ROLE / "tasks" / "validate_topology.yml").read_text(
        encoding="utf-8"
    )
    fixture = (ANSIBLE_ROOT / "tests" / "cilium-contract.yml").read_text(
        encoding="utf-8"
    )

    require(
        "enforce_phase_one_policy.yml" in preflight,
        "preflight must invoke the explicit Phase-1 topology policy",
    )
    require(
        lifecycle.index("preflight.yml")
        < lifecycle.index("install_helm.yml")
        < lifecycle.index("converge.yml"),
        "the Phase-1 guard must execute before Helm installation or mutation",
    )
    require(
        "cilium_phase_one_nodes | length == 1" in policy,
        "Phase 1 must still reject a real multi-node lifecycle",
    )
    require(
        "private-underlay and second-node" in policy,
        "Phase-1 rejection must explain the planned migration boundary",
    )
    for health_field in (
        "desiredNumberScheduled",
        "currentNumberScheduled",
        "updatedNumberScheduled",
        "numberReady",
        "numberAvailable",
    ):
        require(
            health_field in topology,
            f"topology validator is missing DaemonSet field {health_field!r}",
        )
    for forbidden in (
        "desiredNumberScheduled'] == 1",
        "currentNumberScheduled'] == 1",
        "updatedNumberScheduled'] == 1",
        "numberReady'] == 1",
        "numberAvailable'] == 1",
        "cilium_topology_nodes | length == 1",
        "['items'][0]",
        "Cluster health:\\s+1/1 reachable",
    ):
        require(
            forbidden not in validation + topology,
            f"generic health validation contains topology literal {forbidden!r}",
        )
    require(
        topology.count("== cilium_expected_node_count") >= 8,
        "generic health must derive Node, agent, and runtime expectations",
    )
    require(
        'loop: "{{ cilium_agent_pod_names }}"' in validation,
        "runtime status must be collected from every active Cilium agent",
    )
    require(
        "healthy two-node topology is accepted by generic validation" in fixture,
        "the independent synthetic two-node health fixture is missing",
    )
    require(
        "two Kubernetes Nodes remain unsupported in Phase 1" in fixture,
        "the independent Phase-1 multi-node rejection fixture is missing",
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
    validate_cni_package_baseline()
    validate_helm_lifecycle_contract()
    validate_install_path_assertion_contract()
    validate_runtime_proofs()
    validate_topology_policy_separation()
    validate_destructive_safeguards()
    print(
        "Cilium lifecycle contract passed: separation, immutable pins, "
        "fail-closed state machine, idempotence, and runtime proofs intact"
    )


if __name__ == "__main__":
    main()
