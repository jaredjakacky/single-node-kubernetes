variable "ssh_source_cidrs" {
  description = "IPv4 CIDRs permitted to connect to the host over SSH."
  type        = list(string)
  sensitive   = true
  nullable    = false

  validation {
    condition = (
      length(var.ssh_source_cidrs) > 0 &&
      alltrue([
        for cidr in var.ssh_source_cidrs : can(cidrnetmask(cidr))
      ])
    )
    error_message = "ssh_source_cidrs must contain at least one valid IPv4 CIDR."
  }
}
