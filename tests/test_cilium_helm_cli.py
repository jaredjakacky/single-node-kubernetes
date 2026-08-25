#!/usr/bin/env python3

"""Exercise every Cilium Helm invocation with the exact pinned Helm client."""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from typing import Any

import yaml

sys.dont_write_bytecode = True

from test_cilium_lifecycle import CILIUM_ROLE, require, role_helm_commands

HELM_VARIABLE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
HELM_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_ARCHIVE_MEMBERS = {
    "linux-amd64",
    "linux-amd64/LICENSE",
    "linux-amd64/README.md",
    "linux-amd64/helm",
}


def load_mapping(path: pathlib.Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path} must contain a YAML mapping")
    return value


def render_argument(argument: str, variables: dict[str, Any]) -> str:
    previous = None
    while argument != previous:
        previous = argument

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            require(name in variables, f"Helm argument uses unknown variable {name}")
            value = variables[name]
            require(
                isinstance(value, (str, int, float)),
                f"Helm argument variable {name} must be scalar",
            )
            return str(value)

        argument = HELM_VARIABLE.sub(replace, argument)
    require("{{" not in argument, f"Helm argument did not render: {argument}")
    return argument


def operation_name(argv: list[str]) -> str:
    if argv[1] in ("get", "show"):
        return " ".join(argv[1:3])
    return argv[1]


def exercise_role_commands(
    helm_binary: pathlib.Path,
    temporary_directory: pathlib.Path,
    variables: dict[str, Any],
) -> dict[str, str]:
    variables = {**variables, "cilium_helm_binary": str(helm_binary)}
    environment = os.environ.copy()
    environment.update(
        {
            "HELM_CACHE_HOME": str(temporary_directory / "cache"),
            "HELM_CONFIG_HOME": str(temporary_directory / "config"),
            "HELM_DATA_HOME": str(temporary_directory / "data"),
        }
    )
    help_by_operation = {}
    for path, role_argv in role_helm_commands():
        argv = [render_argument(argument, variables) for argument in role_argv]
        result = subprocess.run(
            [*argv, "--help"],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
            timeout=30,
        )
        require(
            result.returncode == 0,
            f"{path}: pinned Helm rejected {argv[1:]}:\n{result.stderr}",
        )
        help_by_operation[operation_name(argv)] = result.stdout
    return help_by_operation


def validate_help_contract(help_by_operation: dict[str, str]) -> None:
    require(
        set(help_by_operation)
        == {
            "get values",
            "install",
            "list",
            "show chart",
            "status",
            "template",
            "upgrade",
            "version",
        },
        "the pinned-binary test must cover every reviewed Helm operation",
    )

    list_help = help_by_operation["list"]
    require(
        "By default, it lists all releases in any status." in list_help,
        "Helm list must retain its Helm 4 all-status default",
    )
    for status in (
        "--deployed",
        "--failed",
        "--pending",
        "--superseded",
        "--uninstalled",
        "--uninstalling",
    ):
        require(status in list_help, f"Helm list help is missing status {status}")
    require(
        "Allowed values: table, json, yaml" in list_help,
        "Helm list must support machine-readable JSON output",
    )
    require(
        "Allowed values: table, json, yaml" in help_by_operation["get values"],
        "Helm get values must support machine-readable JSON output",
    )
    require(
        "--kube-version string" in help_by_operation["template"],
        "Helm template must support the pinned Kubernetes capability version",
    )

    install_help = help_by_operation["install"]
    require("--wait WaitStrategy" in install_help, "Helm install must support wait")
    require("--wait-for-jobs" in install_help, "Helm install must wait for jobs")
    require("--timeout duration" in install_help, "Helm install must be bounded")
    require(
        "--rollback-on-failure" in install_help
        and "rollback (uninstall) the installation upon failure" in install_help,
        "Helm install must remove a failed installation through rollback semantics",
    )

    upgrade_help = help_by_operation["upgrade"]
    require("--wait WaitStrategy" in upgrade_help, "Helm upgrade must support wait")
    require("--wait-for-jobs" in upgrade_help, "Helm upgrade must wait for jobs")
    require("--timeout duration" in upgrade_help, "Helm upgrade must be bounded")
    require("--history-max int" in upgrade_help, "Helm upgrade must bound history")
    require(
        "--rollback-on-failure" in upgrade_help
        and "rollback the upgrade to previous success release upon failure"
        in upgrade_help,
        "Helm upgrade must restore the previous successful release on failure",
    )


def main() -> None:
    defaults = load_mapping(CILIUM_ROLE / "defaults" / "main.yml")
    variables = load_mapping(CILIUM_ROLE / "vars" / "main.yml")
    helm_version = variables["cilium_helm_version"]
    expected_checksum = variables["cilium_helm_archive_sha256"]
    require(
        isinstance(helm_version, str) and HELM_VERSION.fullmatch(helm_version),
        "the authoritative Helm version must be an exact semantic version",
    )
    require(
        isinstance(expected_checksum, str) and SHA256.fullmatch(expected_checksum),
        "the authoritative Helm archive checksum must be lowercase SHA-256",
    )

    with tempfile.TemporaryDirectory(prefix="cilium-helm-cli-") as directory:
        temporary_directory = pathlib.Path(directory)
        archive_path = temporary_directory / "helm.tar.gz"
        url = f"https://get.helm.sh/helm-v{helm_version}-linux-amd64.tar.gz"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "single-node-kubernetes-helm-contract"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            with archive_path.open("wb") as archive_output:
                shutil.copyfileobj(response, archive_output)

        with archive_path.open("rb") as archive_input:
            actual_checksum = hashlib.file_digest(archive_input, "sha256").hexdigest()
        require(
            actual_checksum == expected_checksum,
            "downloaded Helm archive does not match the authoritative SHA-256",
        )

        with tarfile.open(archive_path, mode="r:gz") as archive:
            require(
                {member.name for member in archive.getmembers()}
                == EXPECTED_ARCHIVE_MEMBERS,
                "the pinned Helm archive has an unexpected shape",
            )
            binary_member = archive.getmember("linux-amd64/helm")
            require(binary_member.isfile(), "the pinned Helm binary must be a file")
            binary_input = archive.extractfile(binary_member)
            require(binary_input is not None, "the pinned Helm binary cannot be read")
            helm_binary = temporary_directory / "helm"
            with binary_input, helm_binary.open("wb") as binary_output:
                shutil.copyfileobj(binary_input, binary_output)
            helm_binary.chmod(binary_member.mode & 0o777)

        version_output = subprocess.run(
            [helm_binary, "version", "--short"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        require(
            re.fullmatch(rf"v{re.escape(helm_version)}\+g[0-9a-f]+", version_output)
            is not None,
            f"downloaded Helm reported unexpected version {version_output!r}",
        )

        help_by_operation = exercise_role_commands(
            helm_binary,
            temporary_directory,
            {**defaults, **variables},
        )
        validate_help_contract(help_by_operation)

    print(
        f"Cilium Helm CLI contract passed: checksum-verified Helm {helm_version} "
        "accepted every role invocation and required Helm 4 semantics"
    )


if __name__ == "__main__":
    main()
