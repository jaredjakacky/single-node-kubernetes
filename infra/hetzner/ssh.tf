resource "hcloud_ssh_key" "admin" {
  name       = var.deployment.ssh_key_name
  public_key = trimspace(var.admin_ssh_public_key)
}
