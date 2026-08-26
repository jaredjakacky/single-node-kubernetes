#!/usr/bin/env python3

"""Validate the public Terraform root module's deployment trust boundary."""

from __future__ import annotations

import pathlib

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent.parent
TERRAFORM_ROOT = REPOSITORY_ROOT / "infra" / "hetzner"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read(path: str) -> str:
    return (TERRAFORM_ROOT / path).read_text(encoding="utf-8")


def main() -> None:
    terraform_files = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TERRAFORM_ROOT.glob("*.tf"))
    }
    combined = "\n".join(terraform_files.values())

    require(
        not list((TERRAFORM_ROOT / "keys").glob("*.pub")),
        "operator SSH public keys must not be committed to the public repository",
    )
    for forbidden in (
        '"production"',
        '"k8s-01"',
        '"k8s-01-ipv4"',
        '"nbg1"',
        '"cx23"',
        '"jaredjakacky"',
        "${path.module}/keys/",
    ):
        require(
            forbidden not in combined,
            f"public Terraform contains deployment-specific value {forbidden!r}",
        )

    variables = read("variables.tf")
    require('variable "deployment"' in variables, "typed deployment input is absent")
    require(
        'variable "admin_ssh_public_key"' in variables,
        "administrator public-key input is absent",
    )
    require(
        'variable "ssh_source_cidrs"' in variables,
        "SSH source CIDR input is absent",
    )
    for proof in (
        "system_name",
        "image_name",
        "image_architecture",
        "length(distinct(var.ssh_source_cidrs))",
        'tonumber(split("/", cidr)[1]) == 32',
        "cidrhost(cidr, 0)",
        "^ssh-ed25519 ",
    ):
        require(proof in variables, f"Terraform input validation is missing {proof!r}")

    require(
        "var.deployment.server_name" in read("server.tf"),
        "server identity is not supplied by the deployment object",
    )
    require(
        "var.deployment.primary_ipv4_name" in read("primary_ip.tf"),
        "Primary IP identity is not supplied by the deployment object",
    )
    require(
        "var.deployment.ssh_key_name" in read("ssh.tf")
        and "var.admin_ssh_public_key" in read("ssh.tf"),
        "SSH key resource does not consume the reviewed inputs",
    )
    require(
        "var.ssh_source_cidrs" in read("firewall.tf"),
        "firewall does not consume the reviewed source CIDRs",
    )

    versions = read("versions.tf")
    require("cloud {}" in versions, "Terraform cloud block must remain environment-neutral")
    require("organization =" not in versions, "HCP organization must not be public source")
    require("workspaces {" not in versions, "HCP workspace must not be public source")

    gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    require(
        any(line.strip() == "*tfplan*" for line in gitignore.splitlines()),
        "saved Terraform plans must be ignored by Git",
    )

    print("Terraform deployment security contract passed")


if __name__ == "__main__":
    main()
