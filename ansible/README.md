# Ansible

Terraform provisions the Hetzner infrastructure. Ansible configures the Linux
operating system after the machine has been provisioned. Terraform does not run
Ansible, and Ansible does not manage cloud resources.

`playbooks/node.yml` configures a Kubernetes node. It first applies `roles/base`,
which owns the common Debian host configuration, and then `roles/containerd`,
which installs and validates the container runtime. The playbook is idempotent
and can be run again safely.

The containerd role installs pinned official upstream containerd and runc
releases under versioned `/usr/local/lib` directories and exposes the active
binaries through `/usr/local/bin`. Debian 13's containerd 1.7 package is not
used because this project intentionally tracks the containerd 2.3 LTS release
line. Renovate maintains the containerd 2.3 and runc 1.5 pins without
automerge. Downloads are verified against the matching official upstream
SHA-256 manifests before installation.

containerd uses its concise version 4 configuration with the CRI plugin, the
`io.containerd.runc.v2` runtime, and `SystemdCgroup = true` for this systemd and
cgroup v2 host. This layer installs only the container runtime; Kubernetes and
CNI installation remain future work.

This public repository does not contain production inventory or credentials.
The private deployment repository will provide the production target and its
credentials. `inventory/ci.yml` contains a non-routable placeholder used only
for static validation. Do not use it to run the playbook.

`requirements.txt` contains the Python tooling dependencies.
`requirements.yml` contains the Ansible collection dependencies.

From the repository root, install the dependencies and run the local static
checks with:

```sh
python -m pip install -r ansible/requirements.txt
cd ansible
ansible-galaxy collection install -r requirements.yml
ansible-lint
ansible-playbook --inventory inventory/ci.yml --syntax-check playbooks/node.yml
```
