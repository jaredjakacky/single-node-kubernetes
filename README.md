# Single-Node Kubernetes

A self-managed, single-node Kubernetes cluster running on a cloud VM.

This is intentionally a single-node architecture. It is not highly available:
the control plane and workloads share one failure domain.

The cloud infrastructure is provisioned with Terraform and the cluster is
built with kubeadm and containerd.

This repository contains the infrastructure, Kubernetes configuration, and
operational documentation for the cluster. Live deployment automation and
production credentials are intentionally kept outside this public repository.

## Architecture

- Single cloud VM
- Kubernetes control plane and workloads on the same node
- Terraform-managed infrastructure
- kubeadm-managed Kubernetes
- containerd runtime
- Cilium selected as the CNI; installation not yet implemented

The [Kubernetes and Cilium network architecture](docs/network-architecture.md)
defines the IPv4 address plan, initial VXLAN and cluster-pool IPAM design,
deferred features, and future reusable Ansible configuration contract. Its
machine-readable companion is architecture data only; neither file installs
Cilium or changes infrastructure.

The Ansible lifecycle separates repeatable node convergence from Kubernetes
control-plane bootstrap. Bootstrap uses kubeadm only on a confirmed-fresh host
and validates existing cluster state on later runs. Cilium installation remains
a separate, subsequent lifecycle step.

## Status

Work in progress.
