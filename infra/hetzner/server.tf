locals {
  resource_labels = {
    environment = var.deployment.environment
    system      = var.deployment.system_name
    managed_by  = "terraform"
  }
}

data "hcloud_image" "debian" {
  name              = var.deployment.image_name
  with_architecture = var.deployment.image_architecture
}

resource "hcloud_server" "k8s_01" {
  name        = var.deployment.server_name
  location    = var.deployment.location
  server_type = var.deployment.server_type
  image       = data.hcloud_image.debian.id

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
    ignore_changes  = [ssh_keys]
  }
}
