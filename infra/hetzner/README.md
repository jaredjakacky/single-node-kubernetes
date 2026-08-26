# Hetzner Terraform root module

This root module describes one single-node Kubernetes host while deliberately
remaining independent of a particular production account or operator.

The private deployment repository supplies the following values:

- `TF_CLOUD_ORGANIZATION` and `TF_WORKSPACE` select the HCP Terraform state.
- `TF_VAR_deployment` is a JSON object containing the non-secret resource
  identity, image, location, and server type.
- `TF_VAR_admin_ssh_public_key` contains one Ed25519 public key.
- `TF_VAR_ssh_source_cidrs` contains one or more unique, canonical IPv4 `/32`
  administration networks.

Example local validation input:

```sh
export TF_CLOUD_ORGANIZATION=example
export TF_WORKSPACE=single-node-kubernetes
export TF_VAR_deployment='{
  "environment":"development",
  "system_name":"single-node-kubernetes",
  "server_name":"k8s-dev-01",
  "primary_ipv4_name":"k8s-dev-01-ipv4",
  "ssh_key_name":"development-admin",
  "location":"nbg1",
  "server_type":"cx23",
  "image_name":"debian-13",
  "image_architecture":"x86"
}'
export TF_VAR_admin_ssh_public_key='ssh-ed25519 AAAA... operator@example'
export TF_VAR_ssh_source_cidrs='["192.0.2.10/32"]'
terraform init
terraform plan
```

The `hcloud_server.k8s_01`, `hcloud_primary_ip.k8s_01`, firewall, and SSH-key
resource addresses intentionally retain their existing Terraform identities.
Changing the deployment object therefore changes resource arguments rather
than silently creating an unrelated state graph.

Hetzner injects the selected SSH key only when a server is created. Rotating the
Terraform SSH-key resource does not update an existing host's
`authorized_keys`; key rotation must use a separately reviewed host-access
procedure before removing the previous key.
