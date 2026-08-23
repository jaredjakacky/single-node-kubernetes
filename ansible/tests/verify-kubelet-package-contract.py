#!/usr/bin/env python3
"""Verify the pinned kubelet Debian package's pre-bootstrap filesystem contract."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import tarfile

AR_MAGIC = b"!<arch>\n"
AR_HEADER_SIZE = 60


def read_ar_members(path: Path) -> dict[str, bytes]:
    data = path.read_bytes()
    if not data.startswith(AR_MAGIC):
        raise ValueError(f"{path} is not a Debian ar archive")

    members: dict[str, bytes] = {}
    offset = len(AR_MAGIC)
    while offset < len(data):
        header = data[offset : offset + AR_HEADER_SIZE]
        if len(header) != AR_HEADER_SIZE or header[58:60] != b"`\n":
            raise ValueError(f"invalid ar member header at offset {offset}")
        name = header[:16].decode("ascii").strip().removesuffix("/")
        size = int(header[48:58].decode("ascii").strip())
        start = offset + AR_HEADER_SIZE
        end = start + size
        if end > len(data):
            raise ValueError(f"truncated ar member {name!r}")
        members[name] = data[start:end]
        offset = end + (size % 2)
    return members


def tar_members(data: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        return {member.name.removeprefix("./"): member for member in archive}


def control_fields(data: bytes) -> dict[str, str]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        control_member = next(
            (
                member
                for member in archive.getmembers()
                if member.name.removeprefix("./") == "control"
            ),
            None,
        )
        if control_member is None:
            raise ValueError("Debian package has no control file")
        control = archive.extractfile(control_member)
        if control is None:
            raise ValueError("Debian package has no control file")
        fields: dict[str, str] = {}
        for line in control.read().decode("utf-8").splitlines():
            if not line or line[0].isspace() or ":" not in line:
                continue
            name, value = line.split(":", 1)
            fields[name] = value.strip()
        return fields


def require_member(
    members: dict[str, tarfile.TarInfo],
    path: str,
    *,
    mode: int,
    kind: str,
    size: int | None = None,
) -> None:
    member = members.get(path)
    if member is None:
        raise ValueError(f"package is missing {path}")
    if member.uid != 0 or member.gid != 0:
        raise ValueError(
            f"{path} must be owned by uid/gid 0, got {member.uid}/{member.gid}"
        )
    if member.mode != mode:
        raise ValueError(f"{path} mode must be {mode:04o}, got {member.mode:04o}")
    if kind == "directory" and not member.isdir():
        raise ValueError(f"{path} must be a directory")
    if kind == "file" and not member.isreg():
        raise ValueError(f"{path} must be a regular file")
    if size is not None and member.size != size:
        raise ValueError(f"{path} size must be {size}, got {member.size}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    ar_members = read_ar_members(args.package)
    control_name = next(
        (name for name in ar_members if name.startswith("control.tar")), None
    )
    data_name = next((name for name in ar_members if name.startswith("data.tar")), None)
    if control_name is None or data_name is None:
        raise ValueError("Debian package is missing its control or data archive")

    fields = control_fields(ar_members[control_name])
    expected_control = {
        "Package": "kubelet",
        "Version": args.version,
        "Architecture": "amd64",
    }
    for name, expected in expected_control.items():
        if fields.get(name) != expected:
            raise ValueError(
                f"control {name} must be {expected!r}, got {fields.get(name)!r}"
            )

    members = tar_members(ar_members[data_name])
    require_member(members, "etc/kubernetes", mode=0o775, kind="directory")
    require_member(members, "etc/kubernetes/manifests", mode=0o775, kind="directory")
    require_member(
        members,
        "etc/kubernetes/manifests/.kubelet-keep",
        mode=0o644,
        kind="file",
        size=0,
    )
    require_member(members, "var/lib/kubelet", mode=0o775, kind="directory")
    require_member(
        members,
        "var/lib/kubelet/.kubelet-keep",
        mode=0o644,
        kind="file",
        size=0,
    )

    print(f"validated kubelet Debian package baseline for {args.version}")


if __name__ == "__main__":
    main()
