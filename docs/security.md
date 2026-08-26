# Security model

This project treats the public infrastructure repository as reusable source and
the private deployment repository as the production trust boundary.

## Implemented controls

- GitHub Actions and Docker actions are pinned to immutable commits or image
  digests.
- Terraform providers are constrained and locked.
- Public-source deployments use an exact Git commit that must already be merged
  into the public `main` branch.
- Terraform plans are saved and applied without replanning.
- The permanent cloud firewall exposes only SSH from explicit IPv4 `/32`
  administration addresses.
- Production automation creates a separate runner-scoped SSH firewall and
  deletes it through unconditional cleanup.
- SSH host keys are checked and deployment private keys are written with
  restrictive permissions.
- OpenSSH permits root only through public-key authentication; password,
  keyboard-interactive, host-based, GSSAPI, agent-forwarding, and remote
  forwarding paths are disabled.
- Kubernetes, containerd, runc, Helm, Cilium, validation images, and downloaded
  test artifacts use reviewed version and integrity pins.
- kubeadm bootstrap and Cilium deployment use fail-closed state classification,
  host-local lifecycle locks, runtime validation, and second-run idempotence
  checks.
- Privileged kubeconfigs remain on the control-plane host.

## Trust boundaries

The private deployment repository and its production environment own
credentials, account and workspace selection, operator access CIDRs, production
resource identity, and deployment history. The public repository must not
contain those values.

The Kubernetes node remains a single failure and security domain. A compromise
of the host, root account, control plane, or local etcd can compromise the
entire cluster.

## Residual risks requiring explicit lifecycle work

- Production automation still uses root over SSH. Key-only authentication is
  enforced, but migration to a dedicated automation user with reviewed sudo
  policy remains preferable.
- Kubernetes Secrets are not yet protected by an encryption-at-rest provider.
- API-server audit policy and centralized audit-log retention are not yet
  configured.
- Operating-system security updates and reboot coordination require a dedicated
  maintenance workflow so unattended changes do not unexpectedly interrupt the
  single-node control plane.
- Local etcd, PKI, and workload data need tested backup, restore, and off-host
  retention procedures before important workloads depend on the cluster.
- Upstream package signing keys and same-origin checksum manifests still depend
  on upstream release infrastructure and TLS. Higher-assurance deployments
  should mirror reviewed artifacts into a controlled repository.

These items should be handled as separately tested operational migrations rather
than hidden inside normal convergence.
