resource "hcloud_ssh_key" "admin" {
  name       = "admin"
  public_key = file("${path.module}/keys/admin.pub")
}
