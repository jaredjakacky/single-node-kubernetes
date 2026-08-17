locals {
  resource_labels = {
    environment = "production"
    system      = "single-node-kubernetes"
    managed_by  = "terraform"
  }
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
  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    bootstrap_sh       = filebase64("${path.module}/../../host/bootstrap.sh")
    containerd_config  = filebase64("${path.module}/../../host/containerd-config.toml")
    containerd_service = filebase64("${path.module}/../../host/containerd.service")
  })

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
    ignore_changes = [
      ssh_keys,
      user_data,
    ]
  }
}
