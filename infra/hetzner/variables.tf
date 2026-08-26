variable "deployment" {
  description = "Non-secret deployment identity and Hetzner placement supplied by the private deployment repository."
  type = object({
    environment     = string
    system          = string
    server_name     = string
    primary_ip_name = string
    location        = string
    server_type     = string
    ssh_key_name    = string
  })
  nullable = false

  validation {
    condition = alltrue([
      for value in [
        var.deployment.environment,
        var.deployment.system,
        var.deployment.server_name,
        var.deployment.primary_ip_name,
        var.deployment.location,
        var.deployment.server_type,
        var.deployment.ssh_key_name,
      ] : length(trimspace(value)) > 0
    ])
    error_message = "deployment values must be non-empty strings."
  }

  validation {
    condition = alltrue([
      can(regex("^[a-z0-9][a-z0-9-]{0,62}$", var.deployment.environment)),
      can(regex("^[a-z0-9][a-z0-9-]{0,62}$", var.deployment.system)),
      can(regex("^[a-z0-9][a-z0-9-]{0,62}$", var.deployment.server_name)),
      can(regex("^[a-z0-9][a-z0-9-]{0,62}$", var.deployment.primary_ip_name)),
      can(regex("^[a-z0-9][a-z0-9-]{0,62}$", var.deployment.location)),
      can(regex("^[a-z0-9][a-z0-9-]{0,62}$", var.deployment.server_type)),
      can(regex("^[a-z0-9][a-z0-9-]{0,62}$", var.deployment.ssh_key_name)),
    ])
    error_message = "deployment values must use lowercase letters, numbers, and internal hyphens only."
  }
}

variable "admin_ssh_public_key" {
  description = "Initial Ed25519 administrator public key installed when the server is created."
  type        = string
  nullable    = false

  validation {
    condition = (
      var.admin_ssh_public_key == trimspace(var.admin_ssh_public_key) &&
      can(regex(
        "^ssh-ed25519 [A-Za-z0-9+/]+={0,2}( [^\\r\\n]+)?$",
        var.admin_ssh_public_key,
      ))
    )
    error_message = "admin_ssh_public_key must be one trimmed OpenSSH Ed25519 public key."
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
          !strcontains(cidr, ":") &&
          tonumber(split("/", cidr)[1]) == 32 &&
          cidrhost(cidr, 0) == split("/", cidr)[0],
          false,
        )
      ])
    )
    error_message = "ssh_source_cidrs must contain one or more unique, canonical IPv4 /32 CIDRs."
  }
}
