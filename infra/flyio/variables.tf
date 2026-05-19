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
