# Ansible

Terraform provisions the Hetzner infrastructure. Ansible configures the Linux
operating system after the machine has been provisioned. Terraform does not run
Ansible, and Ansible does not manage cloud resources.

`playbooks/node.yml` configures a Kubernetes node. It applies `roles/base` for
the common Debian host configuration, `roles/cilium_host` for Cilium host
prerequisite validation, `roles/containerd` for the container runtime, and
`roles/kubernetes` for the Kubernetes tooling. The playbook is idempotent and
can be run again safely.

Cilium is the selected CNI. The `cilium_host` role prepares the host boundary
only: it does not initialize Kubernetes or install Cilium. The initial cluster
will be IPv4-only, retain kube-proxy, use Cilium's normal tunneling/routing
datapath, and retain the default iptables-based masquerading mode. The role
fails early if the running architecture, kernel, cgroup v2 hierarchy, eBPF and
BTF support, tunnel and policy-routing support, required netfilter/IP-set
modules, or active IPv4 forwarding cannot support that design.

The base role continues to own cgroup v2 and IPv4 forwarding. Debian's kernel
owns the required built-in capabilities and loadable modules; the Cilium host
role validates them without preloading or persisting modules. Cilium will own
the bpffs mount later through its default `bpf.autoMount.enabled=true` setting,
so Ansible deliberately creates neither an fstab entry nor a systemd mount
unit. `br_netfilter` and the bridge netfilter sysctls are intentionally not
enabled or managed by this role because the selected Cilium datapath does not
require them. Optional kernel modules for L7 and FQDN policy are outside the
selected feature set and are not enabled.

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

Cluster bootstrap is a separate lifecycle. `playbooks/bootstrap.yml` first
revalidates the installed pinned Kubernetes tooling without running package
installation or contacting the package repository, and then applies
`roles/kubernetes_bootstrap`; normal `playbooks/node.yml` convergence never
runs `kubeadm init`. On a fresh host, the bootstrap role renders a declarative
kubeadm configuration and initializes the cluster. On an initialized host, the
original input file is left as a historical record while the role validates
live control-plane state without reinitializing it.

Bootstrap does not use a single file as an idempotence marker. A host is fresh
only when `/etc/kubernetes`, `/var/lib/etcd`, and `/var/lib/kubelet` contain no
state. Completeness requires non-empty, correctly typed PKI, kubeconfig, static
Pod, local etcd, and kubelet state. Authenticated API readiness and live
control-plane validation then prove health. A complete healthy cluster is
validated and left unchanged. Any partial local state, an initialized control
plane whose API does not become ready, or immutable configuration drift causes
a hard failure. The role never runs `kubeadm reset`, deletes cluster state, or
retries initialization automatically.

The kubelet static Pod boundary is fail-closed. `/etc/kubernetes` must be a
root-owned directory that is not writable by group or other users, and
`/etc/kubernetes/manifests` must be root-owned with mode `0700`. Its direct
contents must be exactly kubeadm's four expected `etcd`, API server,
controller-manager, and scheduler manifests. Each manifest must be a non-empty
root-owned regular file with mode `0600`; symlinks, permission or ownership
drift, and every unexpected entry—including editor and backup files—are
rejected. These violations classify existing state as partial. Bootstrap does
not change their metadata or remove unexpected static Pod definitions
automatically.

The role holds an atomic host-local lock under `/run/lock` from initial state
detection through final validation. This prevents independent Ansible
controllers from both acting on the same fresh-state decision. A controller
that cannot acquire the lock fails immediately. If a controller is terminated
without running Ansible cleanup, confirm no bootstrap process is active before
manually removing the abandoned lock; reboot also clears `/run`.

The initial cluster uses kubeadm's v1beta4 configuration API, local stacked
etcd, standard kubeadm-managed PKI, the pinned Kubernetes version from
`roles/kubernetes/defaults/main.yml`, the systemd cgroup driver, and kube-proxy.
External-CA mode is not supported by this single-node lifecycle. The single
control-plane node is created without the default `NoSchedule` taint so it can
host workloads after the CNI is healthy. The API advertise address and stable
control-plane endpoint must be supplied explicitly by production inventory.

Cilium is not installed by bootstrap. Cilium's default cluster-pool IPAM will
own Pod CIDR allocation, so the kubeadm configuration deliberately omits
`networking.podSubnet`. The future Cilium deployment must explicitly choose a
non-overlapping IPv4 pool rather than accept Cilium's broad default. Until the
CNI is installed, the Node may be `NotReady` and CoreDNS may be unavailable;
neither condition is treated as a bootstrap failure. Bootstrap still requires
the Node to report a current `Ready` condition and requires `MemoryPressure`,
`DiskPressure`, and `PIDPressure` to be `False`. It deliberately does not
validate `NetworkUnavailable`; network readiness belongs to the Cilium
lifecycle.

Run the lifecycle in order:

```sh
ansible-playbook --inventory <production-inventory> playbooks/node.yml
ansible-playbook \
  --inventory <production-inventory> \
  --extra-vars kubernetes_bootstrap_advertise_address=<node-ipv4> \
  --extra-vars kubernetes_bootstrap_control_plane_endpoint=<stable-name-or-ip>:6443 \
  playbooks/bootstrap.yml
```

Production inventory should place exactly one host in the
`kubernetes_control_plane` group and set the bootstrap address values there
rather than normally passing them on the command line. The endpoint must
resolve over IPv4 to the advertise address from the node itself. The service
subnet defaults to `10.96.0.0/12`; confirm it and the future Cilium Pod CIDR do
not overlap host, VPN, administration, or other cluster networks before the
first bootstrap. Bootstrap also requires at least 5 GiB and 100,000 inodes free
on each filesystem backing containerd, kubelet, and local etcd; these thresholds
are typed role defaults and can be raised by production inventory. The bootstrap
playbook deliberately rejects Ansible check mode because its runtime health
checks and one-time initialization cannot be represented faithfully that way.

The Terraform firewall currently exposes SSH but not the Kubernetes API. Local
validation over SSH does not require public API access. Choose an explicit
operator access path before exposing TCP 6443: on-host administration, an SSH
tunnel or VPN, or a narrowly allowlisted Terraform firewall rule.

`/etc/kubernetes/admin.conf` and `/etc/kubernetes/super-admin.conf` are
privileged credentials generated by kubeadm. Bootstrap uses `admin.conf`
locally for validation but does not copy either file to a user home, fetch it
to the Ansible controller, or publish it as an artifact. User credentials and
RBAC are a separate lifecycle concern.

If bootstrap reports partial, unhealthy, or inconsistent state, inspect its
kubelet and containerd diagnostics before taking action. Extended journals are
stored only on the host at
`/var/log/single-node-kubernetes/bootstrap-diagnostics.log`, owned by root with
mode `0600`; they are not emitted into normal Ansible output. Repair valuable
state in place, or follow an explicit operator-approved reset/rebuild procedure.
Because this is a single-node cluster with local etcd, destructive recovery can
lose all cluster data. An etcd snapshot and restore procedure, protected PKI
backup, certificate renewal, Kubernetes upgrade, and deliberate rebuild runbook
must be established before production workloads depend on the cluster.

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
ansible-playbook --inventory inventory/ci.yml --syntax-check playbooks/bootstrap.yml
ansible-playbook tests/bootstrap-state.yml
ansible-playbook tests/kubeadm-flags.yml
ansible-playbook tests/node-conditions.yml
ansible-playbook tests/static-manifest-boundary.yml
ansible-playbook \
  --extra-vars kubernetes_bootstrap_test_kubeadm_path=/path/to/pinned/kubeadm \
  tests/kubeadm-config.yml
```
