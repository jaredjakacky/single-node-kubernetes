resource "hcloud_primary_ip" "k8s_01" {
  name        = "k8s-01-ipv4"
  type        = "ipv4"
  location    = "nbg1"
  auto_delete = false
  labels      = local.resource_labels
}
