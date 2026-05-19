provider "fly" {
  # Token is read from FLY_API_TOKEN environment variable.
  # Set in HCP Terraform workspace variables (sensitive).
  # Generate with: fly tokens create deploy
}
