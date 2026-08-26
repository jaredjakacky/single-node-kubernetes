resource "hcloud_firewall" "k8s_01" {
  name   = var.deployment.server_name
  labels = local.resource_labels

  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "22"
    source_ips  = var.ssh_source_cidrs
    description = "SSH administration"
  }
}
