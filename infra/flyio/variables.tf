variable "fly_org" {
  type        = string
  description = "Fly.io organization slug."
  default     = "personal"
}

variable "region" {
  type        = string
  description = "Primary Fly.io region."
  default     = "cdg"
}

variable "volume_size_gb" {
  type        = number
  description = "Size of the persistent volume in GB (stores webhooks.db)."
  default     = 1
}
