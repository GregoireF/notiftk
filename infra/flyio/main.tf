# ── App ──────────────────────────────────────────────────────────────────────

resource "fly_app" "this" {
  name = "notitk"
  org  = var.fly_org
}

# Import existing app created via CLI:
#   tofu import fly_app.this notitk
import {
  to = fly_app.this
  id = "notitk"
}


# ── Notes ─────────────────────────────────────────────────────────────────────
# Machines (compute) are intentionally NOT managed here.
# They are created and updated by `flyctl deploy` in CI/CD (ci.yml → deploy job).
# This keeps infrastructure (app shell + volume) separate from deployment (image).
#
# Persistent volume (notitk_data / vol_vz88wex7359onlxv, 1 GB, cdg) is NOT
# managed here — the fly-apps/fly provider has a known bug with volume import
# (AttributeName("internalid") schema mismatch). The volume was created via CLI
# and is stable; manage it with `fly volumes` commands if needed.
#
# Secrets (API_KEYS, etc.) are also not managed here — they would appear in
# Terraform state in plaintext. Use `fly secrets set` or Doppler instead.
