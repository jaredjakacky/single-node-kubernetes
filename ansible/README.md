# Ansible

Terraform provisions the Hetzner infrastructure. Ansible configures the Linux
operating system after the machine has been provisioned. Terraform does not run
Ansible, and Ansible does not manage cloud resources.

The Ansible controller runs the repository-pinned `ansible-core` with its own
Python runtime; CI currently selects Python 3.14 for that controller. Managed
nodes have a separate runtime contract: they are Debian 13 systems whose base
image already provides the Debian system Python interface at `/usr/bin/python3`.
`ansible.cfg` selects that stable interface explicitly rather than coupling the
inventory to Debian's current Python minor version. The base role remains the
authority that rejects any managed-node operating system other than Debian 13.
Implicit fact injection is disabled, and roles access gathered facts only
through `ansible_facts` so CI exercises the forward-compatible Ansible behavior.

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

The detailed [Kubernetes and Cilium network architecture](../docs/network-architecture.md)
defines the non-overlapping address plan, VXLAN and cluster-pool IPAM choices,
deferred features, permanent identity requirements, and public-role input
contract. The dedicated deployment procedure below implements that contract.

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

Bootstrap does not use a single file as an idempotence marker. State detection
reports all observed paths, separately validates the kubelet package baseline,
and classifies every remaining path as meaningful kubeadm, kubelet, or etcd
state. The only optional baseline files are the pinned kubelet package's two
zero-byte, root-owned `.kubelet-keep` files. Each present file must match its
exact path, type, ownership, mode, size, dpkg owner, and installed pinned
package version. Absence is accepted; a malformed file or any other entry is
not filtered from meaningful state.

A host is fresh only when it has no meaningful state, every present package
baseline file validates, and its existing state directories are known-safe
pre-bootstrap inputs. Only then does bootstrap normalize `/etc/kubernetes` to
`0755` and its `manifests` directory, `/var/lib/kubelet`, and `/var/lib/etcd` to
`0700`, all root-owned. It redetects state and asserts this contract before
preflight or `kubeadm init`. Completeness requires non-empty, correctly typed
PKI, kubeconfig, static Pod, local etcd, and kubelet state. Authenticated API
readiness and live control-plane validation then prove health. A complete
healthy cluster is validated and left unchanged. Any partial local state, an
initialized control plane whose API does not become ready, or immutable
configuration drift causes a hard failure. The role never normalizes completed
state, runs `kubeadm reset`, deletes cluster state, or retries initialization
automatically.

The kubelet static Pod boundary is fail-closed. `/etc/kubernetes` must be a
root-owned directory that is not writable by group or other users, and
`/etc/kubernetes/manifests` must be root-owned with mode `0700`. Its direct
contents must be exactly kubeadm's four expected `etcd`, API server,
controller-manager, and scheduler manifests, plus the optional, independently
validated `/etc/kubernetes/manifests/.kubelet-keep` package file. Each manifest
must be a non-empty root-owned regular file with mode `0600`; symlinks,
permission or ownership drift, and every unexpected entry—including every
other dotfile, editor file, and backup file—are rejected. These violations
classify existing state as partial. Bootstrap does not change their metadata or
remove unexpected static Pod definitions automatically.

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

Cilium is not installed by bootstrap. Cilium's cluster-pool IPAM owns Pod CIDR
allocation, so the kubeadm configuration deliberately omits
`networking.podSubnet`. The Cilium deployment explicitly chooses a
non-overlapping IPv4 pool rather than accepting Cilium's broad default. Until the
CNI is installed, the Node may be `NotReady` and CoreDNS may be unavailable;
neither condition is treated as a bootstrap failure. Bootstrap requires exactly
one determinate Node `Ready` condition, accepting either `True` or `False`, and
requires `MemoryPressure`, `DiskPressure`, and `PIDPressure` to be `False`. It
does not claim heartbeat freshness or `Ready=True`, and it deliberately does not
validate `NetworkUnavailable`; network readiness belongs to the Cilium
lifecycle. Bootstrap validation therefore establishes a healthy kubeadm
control-plane boundary, not a CNI-complete or workload-ready cluster.

Run the lifecycle in order:

```sh
ansible-playbook --inventory <production-inventory> playbooks/node.yml
ansible-playbook \
  --inventory <production-inventory> \
  --extra-vars kubernetes_bootstrap_advertise_address=<node-ipv4> \
  --extra-vars kubernetes_bootstrap_control_plane_endpoint=<stable-name-or-ip>:6443 \
  playbooks/bootstrap.yml
ansible-playbook --inventory <production-inventory> playbooks/cilium.yml
```

Production inventory should place exactly one host in the
`kubernetes_control_plane` group and set the bootstrap address values there
rather than normally passing them on the command line. It must also define the
permanent, topology-neutral `cilium_cluster_name` and unique integer
`cilium_cluster_id` (1 through 255). The role defaults the reserved first-cluster
pool and node allocation to `10.200.0.0/16` and `/24`, but production inventory
should state `cilium_cluster_pool_ipv4_cidr` and
`cilium_cluster_pool_ipv4_mask_size` explicitly so the deployed address plan is
reviewable alongside the private network environment. The endpoint must
resolve over IPv4 to the advertise address from the node itself. The service
subnet defaults to `10.96.0.0/12`; confirm it and the Cilium Pod CIDR do
not overlap host, VPN, administration, or other cluster networks before the
first bootstrap. Bootstrap also requires at least 5 GiB and 100,000 inodes free
on each filesystem backing containerd, kubelet, and local etcd; these thresholds
are typed role defaults and can be raised by production inventory. The bootstrap
playbook deliberately rejects Ansible check mode because its runtime health
checks and one-time initialization cannot be represented faithfully that way.

## Cilium lifecycle

`playbooks/cilium.yml` is the only deployment entry point for cluster
networking. It expects node convergence and a healthy kubeadm bootstrap to have
completed, requires the local root-owned `0600`
`/etc/kubernetes/admin.conf`, verifies Kubernetes 1.36 and the retained
`kube-proxy` DaemonSet, and executes Helm and kubectl only on the control-plane
node. The kubeconfig is never fetched to the Ansible controller.

The role installs official Helm `4.2.4` from `get.helm.sh` under a versioned
`/usr/local/lib/helm` path after checking the pinned archive SHA-256. It installs
Cilium chart `1.20.1` from the official
`oci://quay.io/cilium/charts/cilium` repository by immutable manifest digest.
There is no latest-version lookup, repository bootstrap, or downloaded script.
The canonical user values are kept as root-owned `0600` JSON under
`/etc/single-node-kubernetes`; JSON is valid Helm values input and permits an
exact typed comparison with `helm get values` on every rerun.

The configured release is explicitly IPv4-only, uses cluster-pool IPAM with the
supplied pool and `/24` allocations, VXLAN tunneling, retained kube-proxy,
iptables IPv4 masquerading, Cilium-managed bpffs, and one operator replica.
Hubble, Relay, encryption/WireGuard, Gateway API, Cilium Ingress, BGP,
ClusterMesh, BIG TCP, Envoy, and kube-proxy replacement are disabled. None of
those settings is an opportunistic role toggle. Layer 7 policy is also disabled
while standard Kubernetes NetworkPolicy remains enabled; changing one of these
boundaries requires a separate architecture migration.

Before a chart mutation, the role holds a host-local lock and classifies Helm,
CNI configuration, the Cilium agent/operator/config map, and a representative
Cilium CRD together. The supported states are:

- A genuinely empty CNI boundary installs the pinned release.

- The exact deployed chart with byte-for-type-equivalent canonical values is
  validated without a Helm install or upgrade.

- An older Cilium 1.20 patch with the same canonical values upgrades only when
  `cilium_allow_upgrade=true` is supplied deliberately.

- Configuration drift, another CNI, unmanaged Cilium resources, incomplete
  managed state, failed/pending releases, newer releases, downgrades, and
  cross-minor upgrades fail closed. The role does not adopt, uninstall, delete,
  reset, or reinitialize networking state.

Every successful run proves more than Helm readiness. It requires one Ready
Cilium agent, one healthy operator, healthy `cilium-dbg` Kubernetes/datapath/
IPAM/controllers status, VXLAN routing, iptables IPv4 masquerading, Hubble
disabled, `Node Ready=True`, and Ready CoreDNS Pods. It then creates an isolated
temporary namespace with digest-pinned Kubernetes e2e images and proves:

- a validation Pod receives an IPv4 address inside the configured Pod pool;

- Pod-to-Pod and direct ClusterIP Service connections work;

- a Service FQDN resolves through cluster DNS and connects;

- bounded TCP egress to `registry.k8s.io:443` works; and

- an ingress NetworkPolicy permits a labeled client while a differently
  labeled client times out on the same server and port.

The lifecycle removes only the validation namespace that it created, including
after validation failure. Because those resources leave no retained state,
their commands deliberately do not report Ansible changes. A second run against
the exact release takes the `validate` action and performs no Helm mutation.

On failure, the role emits current Nodes, all Pods, Cilium/CoreDNS workloads,
events, Helm status, `cilium-dbg status`, and validation resources before
returning the failure. It never prints kubeconfig contents or Secret data. If a
run is terminated before Ansible's cleanup executes, confirm that no lifecycle
is active before removing the abandoned lock under `/run/lock`; similarly,
inspect and remove a stale `cilium-validation` namespace only after confirming
ownership. Repair inconsistent valuable state in place or follow a separately
approved Cilium recovery/rollback runbook—do not make the role bypass its state
classification.

Kube-proxy replacement remains a future, independently reviewed migration.
Multi-node underlay/MTU work, redundant operators, Hubble, and WireGuard are
also deferred until their architecture and rollback behavior are implemented
and validated explicitly.

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
python tests/test_network_architecture.py
python tests/test_cilium_lifecycle.py
cd ansible
ansible-galaxy collection install -r requirements.yml
ansible-lint
ansible-playbook --inventory inventory/ci.yml --syntax-check playbooks/node.yml
ansible-playbook --inventory inventory/ci.yml --syntax-check playbooks/bootstrap.yml
ansible-playbook --inventory inventory/ci.yml --syntax-check playbooks/cilium.yml
ansible-playbook tests/cilium-contract.yml
ansible-playbook tests/bootstrap-state.yml
ansible-playbook tests/kubeadm-flags.yml
ansible-playbook tests/node-conditions.yml
ansible-playbook tests/static-manifest-boundary.yml
python tests/verify-kubelet-package-contract.py \
  --version <pinned-package-version> \
  /path/to/pinned-kubelet.deb
ansible-playbook \
  --extra-vars kubernetes_bootstrap_test_kubeadm_path=/path/to/pinned/kubeadm \
  tests/kubeadm-config.yml
```

CI runs `tests/bootstrap-state.yml` through the installed Ansible executable
under `sudo`. This makes the package-baseline fixture literally root-owned and
reproduces the production uid/gid contract; an unprivileged local invocation
uses the invoking uid/gid while exercising the same mismatch checks.
