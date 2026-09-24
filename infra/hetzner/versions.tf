terraform {
  required_version = "~> 1.16.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.68.0"
    }
  }

  cloud {
    organization = "jaredjakacky"

    workspaces {
      name = "single-node-kubernetes"
    }
  }
}
