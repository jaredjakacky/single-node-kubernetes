locals {
  resource_labels = {
    environment = "production"
    system      = "single-node-kubernetes"
    managed_by  = "terraform"
  }

  # Hetzner user_data is a creation-time bootstrap. Changes under host/ do not
  # converge an existing server; day-two upgrades need an explicit lifecycle
  # mechanism, which is intentionally outside this change.
  cloud_init_user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    bootstrap_sh       = filebase64("${path.module}/../../host/bootstrap.sh")
    containerd_config  = filebase64("${path.module}/../../host/containerd-config.toml")
    containerd_service = filebase64("${path.module}/../../host/containerd.service")
  })
}

data "hcloud_image" "debian_13_x86" {
  name              = "debian-13"
  with_architecture = "x86"
}

resource "hcloud_server" "k8s_01" {
  name        = "k8s-01"
  location    = "nbg1"
  server_type = "cx23"
  image       = data.hcloud_image.debian_13_x86.id
  user_data   = local.cloud_init_user_data

  ssh_keys     = [hcloud_ssh_key.admin.id]
  firewall_ids = [hcloud_firewall.k8s_01.id]

  delete_protection  = true
  rebuild_protection = true
  labels             = local.resource_labels

  public_net {
    ipv4_enabled = true
    ipv4         = hcloud_primary_ip.k8s_01.id
    ipv6_enabled = false
  }

  lifecycle {
    prevent_destroy = true

    # hcloud v1.68.0 marks both attributes ForceNew. Ignore creation-time
    # inputs so source or key changes cannot propose replacing the live node.
    ignore_changes = [
      ssh_keys,
      user_data,
    ]

    # This rendered template is ASCII (including base64 file contents), so
    # Terraform's string length is also its UTF-8 byte size.
    precondition {
      condition     = length(local.cloud_init_user_data) <= 32768
      error_message = "Rendered Hetzner user_data exceeds the supported 32 KiB limit."
    }
  }
}
