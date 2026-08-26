resource "hcloud_primary_ip" "k8s_01" {
  name        = var.deployment.primary_ipv4_name
  type        = "ipv4"
  location    = var.deployment.location
  auto_delete = false
  labels      = local.resource_labels
}
