# Ansible

Terraform provisions the Hetzner infrastructure. Ansible configures the Linux
operating system after the machine has been provisioned. Terraform does not run
Ansible, and Ansible does not manage cloud resources.

`playbooks/node.yml` configures a Kubernetes node. It applies `roles/base` for
the common Debian host configuration, `roles/containerd` for the container
runtime, and `roles/kubernetes` for the Kubernetes tooling. The playbook is
idempotent and can be run again safely.

The containerd role installs pinned official upstream containerd and runc
releases under versioned `/usr/local/lib` directories and exposes the active
binaries through `/usr/local/bin`. Debian 13's containerd 1.7 package is not
used because this project intentionally tracks the containerd 2.3 LTS release
line. Renovate keeps containerd on 2.3 LTS while tracking supported stable runc
1.x releases, without automerge. Downloads are verified against the matching
official upstream SHA-256 manifests before installation.

containerd uses its concise version 4 configuration with the CRI plugin, the
`io.containerd.runc.v2` runtime, and `SystemdCgroup = true` for this systemd and
cgroup v2 host.

The Kubernetes role configures the official Kubernetes 1.36 apt repository and
installs matching, exact versions of kubeadm, kubelet, and kubectl. The packages
are held for deliberate upgrades, while Renovate proposes stable Kubernetes
1.36 patch releases without automerge. Normal node convergence installs missing
packages but refuses to change an installed Kubernetes package version. A
desired-version change therefore requires a separate, orchestrated Kubernetes
upgrade workflow.

Cluster bootstrap is outside this role: it does not run `kubeadm init` or select
and configure a CNI. Kubelet service state and configuration are owned by the
package and cluster-bootstrap lifecycle; kubelet may restart while waiting for
kubeadm to supply its configuration.

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
