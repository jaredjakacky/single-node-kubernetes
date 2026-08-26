terraform {
  required_version = "~> 1.15.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.68.0"
    }
  }

  # The private deployment repository supplies TF_CLOUD_ORGANIZATION and
  # TF_WORKSPACE. Keeping this block empty prevents public source from owning a
  # production account or workspace identity.
  cloud {}
}
