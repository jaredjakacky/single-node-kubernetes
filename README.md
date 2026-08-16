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

Additional platform components will be documented as they are added.

## Status

Work in progress.
