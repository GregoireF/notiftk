output "app_hostname" {
  description = "Public hostname of the app."
  value       = "${fly_app.this.name}.fly.dev"
}

