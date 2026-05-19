output "app_hostname" {
  description = "Public hostname of the app."
  value       = "${fly_app.this.name}.fly.dev"
}

output "volume_id" {
  description = "ID of the persistent volume (webhooks.db)."
  value       = fly_volume.data.id
}
