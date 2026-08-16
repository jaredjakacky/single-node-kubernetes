output "server_public_ipv4" {
  description = "Public IPv4 address of the Kubernetes host."
  value       = hcloud_primary_ip.k8s_01.ip_address
}
