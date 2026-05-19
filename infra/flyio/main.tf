# ── App ──────────────────────────────────────────────────────────────────────

resource "fly_app" "this" {
  name = "notiftk"
  org  = var.fly_org
}

# Import existing app created via CLI:
#   tofu import fly_app.this notiftk
import {
  to = fly_app.this
  id = "notiftk"
}


# ── Persistent volume ─────────────────────────────────────────────────────────
# Stores webhooks.db across deploys and restarts.
# Mounted at /data inside the container (see fly.toml [[mounts]]).

resource "fly_volume" "data" {
  name   = "notitk_data"
  app    = fly_app.this.name
  size   = var.volume_size_gb
  region = var.region
}

# Import existing volume created via CLI:
#   tofu import fly_volume.data vol_vz88wex7359onlxv
import {
  to = fly_volume.data
  id = "vol_vz88wex7359onlxv"
}


# ── Notes ─────────────────────────────────────────────────────────────────────
# Machines (compute) are intentionally NOT managed here.
# They are created and updated by `flyctl deploy` in CI/CD (ci.yml → deploy job).
# This keeps infrastructure (app shell + volume) separate from deployment (image).
#
# Secrets (API_KEYS, etc.) are also not managed here — they would appear in
# Terraform state in plaintext. Use `fly secrets set` or Doppler instead.
