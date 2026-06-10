# LLM-Proxy Admin UI — v3.8 Design: editable keys, admin-password change, Usage persistence + auto-refresh

**Status:** design (brainstormed 2026-06-10). Builds on shipped v3.7.1 (`1.17.1`). Branch: `v3.8-keys-auth-usage`. Releases as `1.18.0`.

**Why:** UI-testing feedback. (1) Virtual keys are create/delete-only — the per-key router settings (and alias/budgets/limits) can't be edited, and a new key silently pins a `routing_strategy` the user didn't choose. (2) No way to change the admin password from the UI (it's an env var). (3) The Usage range resets on navigation. (4) Usage needs hands-off auto-refresh.

---

## 1. Editable virtual keys

**Backend:**
- `KeysClient.update_key(payload)` → `POST {base}/key/update` (mirrors `generate_key`; master key stays server-side).
- Route `POST /api/keys/update` (`login_required`) → `make_keys_client().update_key(payload)`. Pass-through, like `create_key`. The payload carries `key` (the **token** from `/key/list`, never the secret) + changed fields. LiteLLM `/key/update` accepts `key_alias`, `models`, `max_budget`, `tpm_limit`, `rpm_limit`, `duration`, `metadata`, `router_settings`.

**Frontend (`Keys.svelte`):**
- `let editingToken = $state(null)`. An **Edit** button per key row → `editKey(k)` pre-fills the create form from the key's current values: `key_alias`, `models`, `max_budget`, `tpm_limit`/`rpm_limit`, expiry, and the per-key **router settings** (`k.router_settings` — strategy + the v3.6 numeric knobs).
- `create()` → branch on `editingToken`: if set, `POST /api/keys/update` with `{key: editingToken, ...fields}` (no one-time-secret banner — nothing new is minted); else `POST /api/keys` (generate) as today. The "＋ New key" button clears `editingToken`.
- **UX fix:** the new-key `router_strategy` defaults to **blank = "inherit global"** (an `option value=""`), so a fresh key never auto-pins a strategy. (This is what silently gave `hindsight-cbr` a per-key `cost-based` override.)

---

## 2. Change admin password (Settings)

**Problem:** the admin password is `ADMIN_PASSWORD_HASH` — an env var fixed at container start; a running process can't rewrite it, and `.env` isn't mounted into the UI container.

**Design — a DB override hash (master/servant-consistent):**
- New table `ui_admin_auth(id int PRIMARY KEY DEFAULT 1, password_hash text NOT NULL, updated_at timestamptz DEFAULT now())` — a single row (`id = 1`).
- New module `app/admin_auth.py`:
  - `async def effective_hash() -> str` — `CREATE TABLE IF NOT EXISTS …`; `SELECT password_hash FROM ui_admin_auth WHERE id=1`; return it if present, **else** `get_settings().admin_password_hash` (env = bootstrap/fallback). Connects via `asyncpg.connect(get_settings().database_url)` (mirrors `config_db`); if no `database_url`, return the env hash.
  - `async def set_hash(h: str)` — `INSERT … ON CONFLICT (id) DO UPDATE`.
  - `def verify_and_hash(old: str, new: str, eff: str) -> str` — **pure, TDD'd**: raise `HTTPException(401)` if `not verify_password(old, eff)`; raise `HTTPException(422)` if `len(new) < 8`; return `hash_password(new)`.
- `auth_routes.login` → **make async**; `eff = await effective_hash()`; verify against `eff` (not `s.admin_password_hash` directly).
- New route `POST /api/auth/change-password` (`login_required`), body `{old_password, new_password}`: `eff = await effective_hash()`; `h = verify_and_hash(old, new, eff)`; `await set_hash(h)`; return `{ok: true}`.

**Security:** verify old before changing; store the argon2 hash only (never plaintext); the env hash remains the bootstrap (first login / DB-cleared fallback). Sessions are signed by `SESSION_SECRET`, **not** the password, so changing it doesn't log anyone out; new logins use the new password. `SESSION_SECRET`/`LITELLM_SALT_KEY` are untouched (no credential-vault impact).

**Frontend (`Settings.svelte`):** a "Change admin password" card — Current / New / Confirm fields → `POST /api/auth/change-password`; inline success/error; client-side check that New == Confirm and length ≥ 8.

---

## 3. Usage — retain the selected range

`Usage.svelte`: persist `days` in **`localStorage["usage.days"]`**. On init, read it (default 30 if absent/invalid); on change, write it. Per-browser UI preference — no backend.

---

## 4. Usage — auto-refresh (client polling, configurable + saved)

`Usage.svelte`: an **Auto-refresh** `<select>` — `Off / 10s / 30s / 60s / 5m` — bound to `refreshSec` (0 = off), persisted in **`localStorage["usage.refreshSec"]`**.
- When `refreshSec > 0`, a `setInterval(load, refreshSec*1000)` re-fetches `/api/usage/summary?days=${days}` and updates the tables in place (Svelte reactivity — no page reload).
- **Pause when hidden:** a `visibilitychange` listener clears the interval while `document.hidden` and restarts it on return, so a backgrounded tab doesn't poll.
- Clear the interval on `refreshSec`/`days` change and on component destroy (no leaks/overlap).

---

## Build phasing (one branch `v3.8-keys-auth-usage`, released `1.18.0`)
1. **Usage persistence + auto-refresh** (#3, #4) — `Usage.svelte` only; smallest, ships value immediately.
2. **Editable virtual keys** (#1) — `KeysClient.update_key` + route, then `Keys.svelte` edit flow + inherit-global default.
3. **Admin-password change** (#2) — `admin_auth.py` (TDD `verify_and_hash`) + async login + change-password route, then `Settings.svelte` card.
4. **Integration + release** — Playwright on the LAN-IP origin: range + interval survive reload and the table updates on the timer; edit a key's router settings/budget via `/key/update`; change the admin password, log out, log in with the new one (and confirm the old fails). Merge → `1.18.0`, bump pin.

## Out of scope
- Rotating/regenerating a key's secret (that's delete + create — the secret is one-time by design).
- Multi-user / role-based admin accounts (single admin password only).
- Server-pushed (SSE) usage — client polling is the right tool for a periodic snapshot (decided in brainstorming).
- Password-strength meters beyond a min-length check.

## Testing
- **Admin password (TDD):** `verify_and_hash` — correct old + valid new → returns a hash where `verify_password(new, h)` is True; wrong old → 401; new shorter than 8 → 422. (Pure; no DB.) Integration: `effective_hash()` returns the DB row when present, else the env hash; login works with the env hash before any change and with the new hash after.
- **Editable keys:** route forwards to `update_key` (mock client asserts payload carries `key` + fields). Integration (Playwright): edit a key's `rpm_limit` + per-key `timeout` → `/key/update` 200 → `/key/list` reflects it.
- **Usage:** Playwright — set 7d + 30s auto-refresh, reload → both restored; issue a request → the table updates within one interval without a manual reload; background the tab → polling pauses.
