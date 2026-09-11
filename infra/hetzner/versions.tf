terraform {
  required_version = "~> 1.15.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.69.0"
    }
  }

  cloud {
    organization = "jaredjakacky"

    workspaces {
      name = "single-node-kubernetes"
    }
  }
}
