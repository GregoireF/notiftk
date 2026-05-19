terraform {
  required_version = ">= 1.9"

  required_providers {
    fly = {
      source  = "fly-apps/fly"
      version = "~> 0.0"
    }
  }

  # HCP Terraform remote backend — mirrors the pattern from iac/terraform/github.
  # Setup:
  #   1. Create a workspace "notiftk-flyio" in your HCP org (execution mode: Remote)
  #   2. Add FLY_API_TOKEN as a workspace environment variable (sensitive)
  #   3. Generate a team token → add as GitHub secret TF_API_TOKEN
  cloud {
    hostname     = "app.terraform.io"
    organization = "gregoiref"

    workspaces {
      name = "notifk-flyio"
    }
  }
}
