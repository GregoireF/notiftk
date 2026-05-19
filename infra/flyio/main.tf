# ── App ──────────────────────────────────────────────────────────────────────

# Remove the old notitk app from state without destroying it on Fly.io.
# The app was recreated with the correct name (notiftk); imported below.
removed {
  from = fly_app.this
  lifecycle {
    destroy = false
  }
}

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


# ── Notes ─────────────────────────────────────────────────────────────────────
# Machines (compute) are intentionally NOT managed here.
# They are created and updated by `flyctl deploy` in CI/CD (ci.yml → deploy job).
# This keeps infrastructure (app shell + volume) separate from deployment (image).
#
# Persistent volume (notiftk_data / vol_40oop652jnkz5pn4, 1 GB, cdg) is NOT
# managed here — the fly-apps/fly provider has a known bug with volume import
# (AttributeName("internalid") schema mismatch). The volume was created via CLI
# and is stable; manage it with `fly volumes` commands if needed.
#
# Secrets (API_KEYS, ADMIN_SECRET, etc.) are also not managed here — they would
# appear in Terraform state in plaintext. Use `fly secrets set` or Doppler instead.
