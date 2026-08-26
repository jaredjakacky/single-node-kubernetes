variable "deployment" {
  description = "Non-secret deployment identity and Hetzner placement supplied by the private deployment repository."
  type = object({
    environment        = string
    system_name        = string
    server_name        = string
    primary_ipv4_name  = string
    ssh_key_name       = string
    location           = string
    server_type        = string
    image_name         = string
    image_architecture = string
  })
  nullable = false

  validation {
    condition = alltrue([
      for value in [
        var.deployment.environment,
        var.deployment.system_name,
        var.deployment.server_name,
        var.deployment.primary_ipv4_name,
        var.deployment.ssh_key_name,
        var.deployment.location,
        var.deployment.server_type,
        var.deployment.image_name,
        var.deployment.image_architecture,
      ] : length(trimspace(value)) > 0
    ])
    error_message = "deployment values must be non-empty strings."
  }

  validation {
    condition = alltrue([
      for value in [
        var.deployment.environment,
        var.deployment.system_name,
        var.deployment.server_name,
        var.deployment.primary_ipv4_name,
        var.deployment.ssh_key_name,
        var.deployment.location,
        var.deployment.server_type,
        var.deployment.image_name,
        var.deployment.image_architecture,
      ] : can(regex("^[a-z0-9][a-z0-9.-]{0,62}$", value))
    ])
    error_message = "deployment values must use lowercase letters, numbers, dots, and internal hyphens only."
  }
}

variable "admin_ssh_public_key" {
  description = "Initial Ed25519 administrator public key installed when the server is created."
  type        = string
  nullable    = false

  validation {
    condition = can(regex(
      "^ssh-ed25519 [A-Za-z0-9+/]+={0,2}( [^\\r\\n]+)?$",
      trimspace(var.admin_ssh_public_key),
    ))
    error_message = "admin_ssh_public_key must contain one OpenSSH Ed25519 public key."
  }
}

variable "ssh_source_cidrs" {
  description = "Unique IPv4 /32 CIDRs permitted to connect to the host over SSH."
  type        = list(string)
  nullable    = false

  validation {
    condition = (
      length(var.ssh_source_cidrs) > 0 &&
      length(distinct(var.ssh_source_cidrs)) == length(var.ssh_source_cidrs) &&
      alltrue([
        for cidr in var.ssh_source_cidrs : try(
          length(regexall(":", cidr)) == 0 &&
          tonumber(split("/", cidr)[1]) == 32 &&
          cidrhost(cidr, 0) == split("/", cidr)[0],
          false,
        )
      ])
    )
    error_message = "ssh_source_cidrs must contain one or more unique, canonical IPv4 /32 CIDRs."
  }
}
