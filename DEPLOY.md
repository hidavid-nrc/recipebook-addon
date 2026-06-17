# Recipe Book — Deploy & Operations (v0.5.0)

## Morning deploy (≈5 minutes)

Your data is already safe (manual HA backup taken). These steps push the
hardened code and migrate the DB to durable storage.

1. **Get the new code into your repo** (`hidavid-nrc/recipebook-addon`):
   ```sh
   # from a fresh checkout or your existing clone:
   # copy the contents of this bundle over the repo, then:
   git add -A
   git commit -m "v0.5.0 — durability, Bring fix, cook log, export, model split"
   git push
   ```
2. **Reload the add-on store** so HA re-reads GitHub:
   HA → Settings → Add-ons → Add-on Store → ⋮ (top right) → **Reload**.
3. **Update the add-on:** open *Recipe Book* → it now shows **0.5.0** → click
   **Update** (rebuilds from the new source; the `BUILD_FROM`/version bump
   guarantees a clean build, no cache roulette).
4. **Watch the log.** You want to see:
   ```
   Migrated existing DB from /data to /share/recipebook
   Starting Recipe Book v0.5.0 (data: /share/recipebook)
   [backup] wrote /backup/recipebook/recipes_YYYYMMDD.db
   ```

## Verify after deploy (the things only you can test live)

- [ ] Open the panel → your **28 recipes load** (migration worked).
- [ ] Sidebar **↓ Export** downloads a JSON of the library.
- [ ] On a recipe, **★ + Mark cooked** logs and the summary updates.
- [ ] Plan a week → **🛍 Bring!** → check `todo.mir-party`. If it errors now,
      the message will name the *real* HA cause (no more blind 500).
- [ ] Re-ingest your library if needed (the Wok set) via the **Add → JSON**
      tab or `POST /api/ingest/push`.

## Routine operations

- **Update:** push to `main` → store Reload → Update. Bump `version:` in
  `config.yaml` (and `BUILD_VERSION` in `Dockerfile`) each release.
- **Backups:** automatic — a consistent copy lands in
  `/backup/recipebook/recipes_YYYYMMDD.db` at boot and daily (last 14 kept),
  and is captured by HA's own backups. Portable JSON anytime via **↓ Export**.
- **Restore:** copy a `/backup/recipebook/recipes_*.db` over
  `/share/recipebook/recipes.db` and restart the add-on.

## ⚠️ Critical
- **Never Uninstall.** Uninstall wipes `/data`. The DB now lives in `/share`
  (survives), but treat Uninstall as forbidden anyway. Restart/Rebuild/Update
  are all safe.
- **Rotate the API keys** that were exposed earlier, and paste the new ones
  into the add-on Options.
