# Single-Node Kubernetes

A self-managed, single-node Kubernetes cluster running on a cloud VM.

This is intentionally a single-node architecture. It is not highly available:
the control plane and workloads share one failure domain.

The cloud infrastructure is provisioned with Terraform and the cluster is
built with kubeadm and containerd.

This repository contains the infrastructure, Kubernetes configuration, and
operational documentation for the cluster. Live production inventory,
credentials, and execution state are intentionally kept outside this public
repository.

## Architecture

- Single cloud VM
- Kubernetes control plane and workloads on the same node
- Terraform-managed infrastructure
- kubeadm-managed Kubernetes
- containerd runtime
- Deterministic, separately invoked Cilium lifecycle

The [Kubernetes and Cilium network architecture](docs/network-architecture.md)
defines the IPv4 address plan, initial VXLAN and cluster-pool IPAM design,
deferred features, and reusable Ansible configuration contract. Its
machine-readable companion is normative architecture data rather than a Helm
values file.

The Ansible lifecycle separates repeatable node convergence from Kubernetes
control-plane bootstrap. Bootstrap uses kubeadm only on a confirmed-fresh host
and validates existing cluster state on later runs. The dedicated Cilium
playbook remains a separate, subsequent lifecycle step and performs end-to-end
network validation. See [the Ansible guide](ansible/README.md) for invocation
and recovery details.

## Status

Work in progress.
