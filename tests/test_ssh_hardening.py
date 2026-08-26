#!/usr/bin/env python3

"""Validate the base role's fail-closed OpenSSH security contract."""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE_ROLE = REPOSITORY_ROOT / "ansible" / "roles" / "base"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_yaml(path: pathlib.Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def named_task(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [task for task in tasks if task.get("name") == name]
    require(len(matches) == 1, f"expected one task named {name!r}, found {len(matches)}")
    return matches[0]


def parse_directives(content: str) -> dict[str, str]:
    directives: dict[str, str] = {}
    for line_number, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition(" ")
        require(separator == " " and value.strip(), f"invalid sshd directive on line {line_number}")
        require(name not in directives, f"duplicate sshd directive: {name}")
        directives[name] = value.strip()
    return directives


def main() -> None:
    tasks = load_yaml(BASE_ROLE / "tasks" / "main.yml")
    handlers = load_yaml(BASE_ROLE / "handlers" / "main.yml")
    require(isinstance(tasks, list), "base tasks must be a YAML sequence")
    require(isinstance(handlers, list), "base handlers must be a YAML sequence")

    hardening = named_task(tasks, "Harden OpenSSH authentication and forwarding")
    arguments = hardening.get("ansible.builtin.copy")
    require(isinstance(arguments, dict), "OpenSSH hardening must use ansible.builtin.copy")
    require(
        arguments.get("dest") == "/etc/ssh/sshd_config.d/00-single-node-kubernetes.conf",
        "OpenSSH hardening destination drifted",
    )
    require(arguments.get("owner") == "root", "OpenSSH policy must be root-owned")
    require(arguments.get("group") == "root", "OpenSSH policy must be root-grouped")
    require(arguments.get("mode") == "0644", "OpenSSH policy mode must be 0644")
    require(
        arguments.get("validate") == "/usr/sbin/sshd -t -f %s",
        "OpenSSH policy must be syntax-checked before installation",
    )
    require(
        hardening.get("notify") == "Validate and reload OpenSSH",
        "OpenSSH policy must notify the validated reload handler",
    )

    content = arguments.get("content")
    require(isinstance(content, str), "OpenSSH policy content must be a string")
    expected = {
        "PermitRootLogin": "prohibit-password",
        "AuthenticationMethods": "publickey",
        "PubkeyAuthentication": "yes",
        "PasswordAuthentication": "no",
        "KbdInteractiveAuthentication": "no",
        "HostbasedAuthentication": "no",
        "GSSAPIAuthentication": "no",
        "PermitEmptyPasswords": "no",
        "PermitUserEnvironment": "no",
        "PermitUserRC": "no",
        "MaxAuthTries": "3",
        "LoginGraceTime": "30",
        "AllowAgentForwarding": "no",
        "AllowTcpForwarding": "local",
        "AllowStreamLocalForwarding": "local",
        "GatewayPorts": "no",
        "PermitTunnel": "no",
        "X11Forwarding": "no",
        "ClientAliveInterval": "300",
        "ClientAliveCountMax": "2",
        "LogLevel": "VERBOSE",
    }
    require(parse_directives(content) == expected, "OpenSSH hardening directives drifted")

    effective_read = named_task(tasks, "Read the effective OpenSSH policy")
    require(
        effective_read.get("ansible.builtin.command", {}).get("argv")
        == [
            "/usr/sbin/sshd",
            "-T",
            "-C",
            "user=root,host=localhost,addr=127.0.0.1",
        ],
        "effective OpenSSH policy must be evaluated for the root SSH context",
    )
    require(effective_read.get("changed_when") is False, "sshd policy read must not change state")

    effective_assert = named_task(tasks, "Require the effective OpenSSH policy")
    assertions = effective_assert.get("loop")
    require(isinstance(assertions, list), "effective OpenSSH assertions must use a literal loop")
    for required in (
        "authenticationmethods publickey",
        "passwordauthentication no",
        "kbdinteractiveauthentication no",
        "allowtcpforwarding local",
        "allowagentforwarding no",
    ):
        require(required in assertions, f"effective OpenSSH proof is missing {required!r}")

    root_assert = named_task(tasks, "Require key-only root SSH in the effective policy")
    require(
        "permitrootlogin" in str(root_assert.get("ansible.builtin.assert", {}).get("that", "")),
        "effective root-login policy is not asserted",
    )

    require(
        [handler.get("name") for handler in handlers]
        == ["Validate the effective OpenSSH configuration", "Reload the OpenSSH daemon"],
        "OpenSSH handlers must validate before reloading",
    )
    validate_handler, reload_handler = handlers
    require(
        validate_handler.get("ansible.builtin.command", {}).get("argv")
        == ["/usr/sbin/sshd", "-t"],
        "effective OpenSSH validation command drifted",
    )
    require(validate_handler.get("changed_when") is False, "sshd validation must not change state")
    require(
        reload_handler.get("ansible.builtin.systemd_service") == {"name": "ssh", "state": "reloaded"},
        "OpenSSH must be reloaded through Debian's ssh service",
    )
    require(
        all(handler.get("listen") == "Validate and reload OpenSSH" for handler in handlers),
        "OpenSSH handlers must share the reviewed listen topic",
    )

    print("OpenSSH hardening contract passed")


if __name__ == "__main__":
    main()
