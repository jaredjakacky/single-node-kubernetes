# Single-Node Kubernetes

A self-managed, single-node Kubernetes cluster running on a cloud VM.

This is intentionally a single-node architecture. It is not highly available:
the control plane and workloads share one failure domain.

The cloud infrastructure is provisioned with Terraform and the cluster is built
with kubeadm, containerd, and Cilium.

## Repository boundary

This public repository contains reusable Terraform, Ansible roles, architecture
contracts, tests, and operational documentation. The private deployment
repository owns production resource identity, HCP Terraform selection,
credentials, operator access CIDRs, saved plans, and deployment history.

No production SSH key, inventory, account name, workspace name, or operator
network is intended to live in this repository.

## Lifecycle

1. Terraform provisions the Hetzner server, retained Primary IPv4, SSH-only
   firewall, and initial administration key.
2. `playbooks/node.yml` converges the Debian host, enforces key-only SSH,
   installs containerd, and installs pinned Kubernetes tooling.
3. `playbooks/bootstrap.yml` initializes only a confirmed-fresh kubeadm control
   plane and validates healthy existing state on later runs.
4. `playbooks/cilium.yml` installs or validates the digest-pinned Cilium release
   and proves Pod, Service, DNS, egress, and NetworkPolicy behavior.

The [Kubernetes and Cilium network architecture](docs/network-architecture.md)
defines the address plan, initial VXLAN and cluster-pool IPAM design, deferred
features, and reusable Ansible input contract. Its machine-readable companion is
normative architecture data rather than a Helm values file.

See the [Ansible guide](ansible/README.md), the
[Terraform root-module guide](infra/hetzner/README.md), and the
[security model](docs/security.md) for invocation, trust boundaries, and
remaining operational work.

## Verification

Pull requests run Terraform formatting and validation, immutable-workflow
policy checks, Python security and architecture contracts, Ansible lint,
playbook syntax checks, package fixtures, and kubeadm/Cilium lifecycle tests.
Renovate tracks standard dependency files and reviewed custom pins without
automerge.

## Current status

The infrastructure, node-convergence, kubeadm-bootstrap, and Cilium lifecycles
are implemented and have passed production deployment and idempotence checks.
The project remains intentionally single-node and still requires dedicated
backup/restore, operating-system maintenance, Kubernetes upgrade, Cilium
upgrade, and non-root automation-user procedures before it should host
irreplaceable workloads.
