# Kubernetes and Cilium network architecture

This document is the design boundary for the first Cilium lifecycle. It records
the network decisions that a later implementation must follow, but it does not
install Cilium, create a Hetzner private network, or change the live cluster.
The companion [`network-contract.json`](network-contract.json) is normative,
machine-readable design data. It is not a Helm values file or an input to any
production workflow.

## Current boundary

The current cluster is one kubeadm control-plane node on one IPv4-only Hetzner
VM. kubeadm already uses the cluster name `single-node-kubernetes` and the
Service CIDR `10.96.0.0/12`. Those values are live cluster state. This design
does not rename the kubeadm cluster, change its Service CIDR, set a kubeadm Pod
subnet, expose the Kubernetes API, or alter Terraform.

Cilium will own Pod address allocation, so kubeadm deliberately continues to
omit `networking.podSubnet`. The first Cilium installation will retain
kube-proxy. The repository's existing `cilium_host` role remains limited to
validating host and kernel prerequisites; it is not a deployment role.

## Underlay and overlay

The **underlay** is the network that connects Kubernetes nodes themselves. It
carries node addresses and must provide ordinary IP reachability between the
VMs. Today that is the single VM's public IPv4 network. A future Hetzner private
node network is reserved as `10.10.0.0/16`, but no such network exists yet.

The **overlay** carries Pod traffic over that underlay. In Cilium tunnel mode,
an inter-node Pod packet is wrapped in a VXLAN packet, sent between the source
and destination node addresses, and unwrapped on the destination node. The
underlay therefore needs routes only for node addresses; it does not need to
learn every node's Pod CIDR. This separation is why VXLAN is the initial routing
choice: it works on the current VM and extends predictably when more Hetzner VMs
join, without first requiring cloud routes or BGP.

VXLAN is not cost-free. Encapsulation reduces the effective payload MTU and
future node firewalls must permit Cilium's VXLAN traffic between node addresses
(UDP 8472 with the standard Cilium configuration). A multi-node implementation
must validate both requirements. Native routing or BGP can be evaluated later
if measured throughput, latency, or routing integration justifies the added
underlay coupling.

## Address plan

| Purpose | CIDR | Lifecycle |
| --- | --- | --- |
| Kubernetes Services | `10.96.0.0/12` | Existing, immutable bootstrap state |
| Current cluster Pods | `10.200.0.0/16` | Initial Cilium cluster pool |
| Per-node Pod allocation | `/24` | Allocated from the current Pod pool |
| Future Hetzner private node network | `10.10.0.0/16` | Reserved only; not provisioned |
| Cluster Pod planning block | `10.200.0.0/13` | Repository-level reservation for eight `/16` pools |

The `10.200.0.0/13` planning block contains `10.200.0.0/16` through
`10.207.0.0/16`. This cluster takes the first `/16`; the other seven `/16`s are
reserved as candidate Pod pools for future clusters. The reservation is an
address-planning rule, not a cloud resource. A future cluster must still verify
its chosen pool against every network reachable from that cluster, including
host, VPN, administration, peering, and external service networks.

Pod CIDRs and the Service CIDR must not overlap. A Pod address represents an
endpoint routed through the CNI, while a Service address is a virtual
destination translated by the Kubernetes service datapath. Reusing an address
in both domains would make forwarding intent ambiguous and can make either the
Pod or Service unreachable. The future node underlay also has its own disjoint
range so a route to a node address cannot compete with a route to a Pod.

Non-overlapping Pod pools across clusters are deliberate preparation for
ClusterMesh and other inter-cluster connectivity. Cilium can evolve and some
topologies can accommodate overlaps, but unique ranges keep routing,
troubleshooting, policy, and service access comprehensible and avoid a later
renumbering project.

## Cluster-pool IPAM

Cilium's cluster-pool IPAM mode gives the operator the cluster-wide
`10.200.0.0/16` pool. The operator assigns a distinct `/24` Pod CIDR to each
`CiliumNode`, and the agent on that node allocates Pod addresses from its
assigned block. For example, three nodes could receive:

| Node | Illustrative allocation |
| --- | --- |
| `k8s-01` | `10.200.0.0/24` |
| `k8s-02` | `10.200.1.0/24` |
| `k8s-03` | `10.200.2.0/24` |

These examples explain the prefix math; they do not promise a hostname-to-CIDR
mapping or allocation order. A `/16` contains 256 distinct `/24` blocks. Each
block contains 256 addresses, but not every address should be treated as Pod
capacity because Cilium reserves addresses for node-local functions and other
operational limits apply. The `/24` mask size is a day-zero decision: changing
the allocation geometry after nodes have received CIDRs is a migration, not a
routine configuration edit.

## Initial Cilium lifecycle

| Area | Initial decision | Why |
| --- | --- | --- |
| Address family | IPv4 only | Matches the IPv4-only Hetzner VM and avoids an untested dual-stack lifecycle. |
| IPAM | Cluster pool | Lets Cilium allocate independent node Pod CIDRs without kubeadm owning `podSubnet`. |
| Routing | Tunnel mode with VXLAN | Keeps Pod routes out of the Hetzner underlay and provides a portable multi-node path. |
| kube-proxy | Retained | Separates CNI introduction from a Kubernetes Service datapath migration and gives the first rollout a smaller failure domain. |
| Masquerading | IPv4 masquerading enabled; BPF masquerading disabled | Starts with Cilium's iptables-backed path already covered by the host prerequisites. BPF masquerading remains a later optimization. |
| Operator replicas | `1` | Extra replicas on one node do not create a second failure domain. Increase redundancy when the cluster has nodes in distinct failure domains. |

The first lifecycle explicitly leaves Hubble, Hubble Relay, WireGuard
transparent encryption, Gateway API, Cilium Ingress, the BGP control plane,
ClusterMesh, kube-proxy replacement, and BIG TCP disabled. These are sequenced
improvements, not permanent rejections.

Hubble is deferred until the second Kubernetes node. At that point,
cluster-wide flow visibility becomes materially useful for proving and
troubleshooting cross-node traffic. WireGuard is a likely hardening step after
ordinary multi-node VXLAN networking is first demonstrated, so encryption does
not obscure the initial routing validation. Gateway, ingress, BGP, ClusterMesh,
kube-proxy replacement, and performance tuning each need their own requirements,
rollback plan, and focused validation.

## Permanent Cilium identity

Cilium's cluster identity is separate from kubeadm's already-live
`clusterName`. The future installation must set both a human-readable Cilium
cluster name and a numeric cluster ID at the first deployment. They must remain
stable as this cluster grows and must be unique if ClusterMesh is introduced.

The Cilium cluster name must be at most 32 characters, start and end with a
lowercase alphanumeric character, and contain only lowercase alphanumeric
characters and hyphens. It must not be derived from `k8s-01`, contain the
phrase `single-node`, or otherwise encode today's topology. The numeric ID must
be an explicit value from 1 through 255. The private live repository will
provide both identity values from production inventory; this public repository
does not choose arbitrary production identity.

## Future Ansible role contract

A future public Cilium deployment role must expose the following inputs and
validate them before running Helm. The names follow current repository
conventions but can be adjusted deliberately when the role exists.

| Input | Contract |
| --- | --- |
| `cilium_cluster_name` | Required production-inventory value; stable, topology-neutral, ClusterMesh-unique, and valid for Cilium's cluster-name syntax. No reusable default. |
| `cilium_cluster_id` | Required production-inventory integer from 1 through 255; stable and ClusterMesh-unique. No reusable default. |
| `cilium_cluster_pool_ipv4_cidr` | IPv4 network, initially `10.200.0.0/16`; must not overlap Services, node networks, VPNs, administration networks, or another cluster Pod pool. |
| `cilium_cluster_pool_ipv4_mask_size` | Integer, initially `24`; must be a longer prefix than the cluster pool and chosen before node allocations exist. |
| `cilium_chart_version` | Required exact chart version. The implementation PR must select a Kubernetes-compatible version, pin it rather than use `latest`, and make the pin Renovate-manageable. |
| `cilium_routing_mode` | Initially constrained to `tunnel`. A change to native routing is an architecture migration. |
| `cilium_tunnel_protocol` | Initially constrained to `vxlan`. The pinned chart's values schema must be the authority for the final Helm mapping. |

Reusable role code must not contain a production cluster name or ID. The role
may encode safe architecture defaults such as the current pool and `/24` mask,
but the live inventory remains the authority for permanent identity. The later
implementation must render explicit values for every initial decision rather
than rely on chart defaults that can change between releases.

## Migration sequence

1. Select and pin a Cilium chart version compatible with the pinned Kubernetes
   release, then implement the reusable role and private identity injection.
2. Install the minimal IPv4/VXLAN lifecycle while kube-proxy remains active;
   validate Pod, Service, DNS, egress, restart, and rollback behavior.
3. Add a second node and its underlay connectivity, including MTU and UDP 8472
   validation, then prove ordinary cross-node traffic.
4. Introduce Hubble for cross-node flow visibility.
5. Evaluate WireGuard as the next multi-node hardening step.
6. Treat operator redundancy, kube-proxy replacement, BPF masquerading, native
   routing or BGP, gateways, ClusterMesh, and performance features as separate
   migrations with measured need and rollback coverage.

## References

- [Cilium cluster-pool IPAM](https://docs.cilium.io/en/stable/network/kubernetes/ipam-cluster-pool/)
- [Cilium routing and encapsulation](https://docs.cilium.io/en/stable/network/concepts/routing/)
- [Cilium masquerading modes](https://docs.cilium.io/en/stable/network/concepts/masquerading/)
- [Cilium ClusterMesh identity requirements](https://docs.cilium.io/en/stable/network/clustermesh/clustermesh/)
- [Cilium Helm values](https://docs.cilium.io/en/stable/helm-values/)
