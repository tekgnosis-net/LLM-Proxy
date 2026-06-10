# LLM-Proxy Admin UI — v3.6 Design: per-key router knobs, model key-validation, editable Provider Keys

**Status:** design (brainstormed 2026-06-10). Builds on shipped v3.5 (`1.15.0`). Branch: `v3.6-keys-validation`.

**Why:** UI-testing feedback. Per-key Router Settings shipped with only strategy+fallbacks (the reliability knobs are valid per-key too). A keyless deployment silently shipped and failed at request time (no guardrail). Provider Keys can't be edited (delete + recreate only), unlike models. The user will reuse one `DUMMY_KEY` credential for local providers (vLLM/llama.cpp), so no inline-key feature is needed.

---

## 1. Per-key router knobs (Virtual Keys)

**Problem:** the per-key Router Settings section (v3.5) exposes only `routing_strategy` + `fallbacks`. LiteLLM's `/key/generate` `router_settings` object also accepts `num_retries`, `timeout`, `cooldown_time`, `allowed_fails`, `retry_after` (confirmed by capturing LiteLLM's own request).

**Design:** extend the "Router Settings (optional)" block in `Keys.svelte` with five numeric inputs — **Num retries, Timeout (s), Cooldown time (s), Allowed fails, Retry after (s)** — each optional (blank = inherit the global setting). In `create()`, add each non-empty value (as `Number`) into the `router_settings` object already assembled for the payload. No backend change (`keys_routes.create_key` forwards the payload). Frontend-only.

---

## 2. Validate a model has *some* key before save (Models)

**Problem:** a model can be saved with no credential and no API-key env var; LiteLLM still requires a key (even for local providers), so the deployment fails at request time with no UI warning. (This is the failure the user hit — a keyless `custom_openai` deployment.)

**Design (frontend-only, non-blocking warning):** in `Models.svelte` `saveModel()`, before staging, if **`!form.credential && !form.api_key_env`**, show a clear inline warning and require a second confirm (don't hard-block — an edge case may not need it):
> "This deployment has no API key. LiteLLM requires one even for local providers (vLLM/llama.cpp) — requests will fail without it. Pick a saved credential (a reusable dummy key works), or set an API-key env var. Save anyway?"
Implement as a `pendingNoKey` state: first Save with no key sets `pendingNoKey=true` and shows the warning + a "Save anyway" button; the second click proceeds. Selecting a credential/env-var clears it. (No change to providers that genuinely need no key — LiteLLM's requirement is uniform, so the warning applies to all; the confirm makes it non-obstructive.)

---

## 3. Editable Provider Keys

**Problem:** credentials can't be edited — only deleted and re-added. Models gained edit-in-place in v3.5; credentials should match.

**Design:**
- **Frontend (`ProviderKeys.svelte`):** each non-deleted credential row gets an **Edit** button → opens the add-form pre-filled with `name` + `provider`; the key field is **blank with placeholder "leave blank to keep the current key"** (the stored key is `***`/encrypted and can't be read back). An `editingName` state marks edit mode. On Save: `stageItem('credential', editingName, { provider, api_key })` — re-stages under the **same name** (a `changed` flag). The "＋ Add key" button clears `editingName` for a fresh add. Editing the **name** field is disabled in edit mode (renaming = delete + re-add; keeps identity simple).
- **Backend (`config_v3_routes.py` `stage_item`, credential branch):** today it 422s when `api_key` is missing. Change it to **reuse the existing encrypted key when blank**:
  ```python
      if kind == "credential":
          provider = (data or {}).get("provider")
          api_key = (data or {}).get("api_key")
          if api_key:
              value_encrypted = _fernet().encrypt(api_key.encode()).decode()
          else:
              store = make_config_store()
              eff = effective(await store.applied(), await store.staged())
              existing = next((i for i in eff if i["kind"] == "credential" and i["name"] == name
                               and i.get("flag") != "deleted"), None)
              ve = (existing or {}).get("data", {}).get("value_encrypted") if existing else None
              if not ve:
                  raise HTTPException(status_code=422, detail="credential api_key required (no existing key to keep)")
              value_encrypted = ve
          data = {"provider": provider, "value_encrypted": value_encrypted}
  ```
  So a blank key on **edit** keeps the secret (lets you change the provider or re-stage without re-typing); a blank key on a **new** credential is still rejected. `***` redaction and "never read the secret back to the browser" are preserved.

**Dropped from the earlier plan:** the inline-API-key-on-model field and the `_check_no_literal_secrets` relaxation — the user's `DUMMY_KEY`-reuse pattern + editable credentials covers the local-provider case with no change to the secret model.

---

## Build phasing (one branch `v3.6-keys-validation`, released as `1.16.0`)
1. **Editable Provider Keys** (#3) — backend stage `keep-existing-key` (TDD via the route's `make_config_store` seam + a fake store), then `ProviderKeys.svelte` edit flow.
2. **Per-key router knobs** (#1) — `Keys.svelte`.
3. **Model key-validation** (#2) — `Models.svelte`.
4. **Integration + release** — local-build Playwright on a LAN-IP origin (v3.4 lesson): edit a credential's provider without re-typing the key (key preserved through Apply); create a key with all router knobs (payload carries them); save a keyless model → warning → "Save anyway" works. Merge → `1.16.0`.

## Out of scope
- Inline API key on the model (dropped — see above).
- Renaming a credential in place (rename = delete + re-add).
- Team-level router settings.

## Testing
- **Backend (TDD):** staging a credential with a blank `api_key` when one already exists reuses `value_encrypted` (provider updatable); blank `api_key` with no existing credential → 422; a provided key encrypts normally. (Route test with a fake/seeded store via `make_config_store`.)
- **Frontend/Integration (Playwright, LAN-IP):** Edit a credential → change provider, leave key blank → Apply → the materialized `config.yaml` credential_list still has the original key (verify via a container read or a successful test-connection). Per-key router knobs land in the `/key/generate` payload. Keyless-model Save shows the warning and "Save anyway" proceeds.
