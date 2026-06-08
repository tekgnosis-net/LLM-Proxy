## [1.13.1](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.13.0...v1.13.1) (2026-06-08)


### Bug Fixes

* **deploy:** config-init service + dir-mount so a fresh deploy can't crash on missing config.yaml ([e69d77a](https://github.com/tekgnosis-net/LLM-Proxy/commit/e69d77adaa508ee14e947f42bd2c7d78259b6455))

# [1.13.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.12.0...v1.13.0) (2026-06-08)


### Bug Fixes

* **ui:** Provider Keys save crashed with 'assign to readonly property' ([0a652ec](https://github.com/tekgnosis-net/LLM-Proxy/commit/0a652ec886e0f57552e2a5d8227eee150a8469a6))
* **ui:** provider picker — clear stale deployment fields on switch; api_base for openai-compatible; datalist labels ([ccae7c0](https://github.com/tekgnosis-net/LLM-Proxy/commit/ccae7c0380f0ee4841a754065a660c23d032bfec))
* **ui:** tighten apply commit boundary — readback temp before os.replace; guard fold; abort tests ([63547cb](https://github.com/tekgnosis-net/LLM-Proxy/commit/63547cb58077d7b9a81327311712f0283448b0d6))


### Features

* **ui:** App apply/discard bar on item-model store ([d9ddac9](https://github.com/tekgnosis-net/LLM-Proxy/commit/d9ddac9a6b6f27210066a3764a09e0307cef5719))
* **ui:** bootstrap-import config.yaml into the config DB on first run ([a5cb119](https://github.com/tekgnosis-net/LLM-Proxy/commit/a5cb119f5ccbe3abba220cb1d2809d99b2f20342))
* **ui:** Caching read-only panel from litellm_setting items ([6422f85](https://github.com/tekgnosis-net/LLM-Proxy/commit/6422f8558f741cb9ba9faa8a741089d20ecd7d68))
* **ui:** catalog providers expose supported modes (endpoints->modes) ([e4c5390](https://github.com/tekgnosis-net/LLM-Proxy/commit/e4c5390fe4a121d559c22b69868c416fb5a935ce))
* **ui:** catalog-driven provider picker (full list, modes filter, advanced api_base) ([e670e1c](https://github.com/tekgnosis-net/LLM-Proxy/commit/e670e1cc0460c78f7c98a35028330eee41a7fe7d))
* **ui:** config_db ConfigStore (applied/staged tables, stage/fold/clear/seed) ([e4c8835](https://github.com/tekgnosis-net/LLM-Proxy/commit/e4c8835c5dac11044f61c0716922ae745dd0c550))
* **ui:** config_engine.apply_config (commit-at-write, fold, no rollback) + pending ([1bd9064](https://github.com/tekgnosis-net/LLM-Proxy/commit/1bd9064d1fea842866dcd20aa7ddef59b8c68354))
* **ui:** config_import.split_config (config.yaml → items + passthrough) ([26cfc65](https://github.com/tekgnosis-net/LLM-Proxy/commit/26cfc65e5cd358f0720afc0affa31ff06ca62a4e))
* **ui:** config_render.effective (applied ⊕ staged with flags) ([eda4d99](https://github.com/tekgnosis-net/LLM-Proxy/commit/eda4d9918c0c048e00f9af0051e6c18579b443d6))
* **ui:** config_render.render_config + redact (items → config.yaml dict) ([ddf714c](https://github.com/tekgnosis-net/LLM-Proxy/commit/ddf714c82ae83101eb66b3b6c82f271fe290bb2a))
* **ui:** Discard button — revert staged changes (no restart) ([bd578e1](https://github.com/tekgnosis-net/LLM-Proxy/commit/bd578e10222d6d6197c3b9b9befa7a1b3df70916))
* **ui:** GET /api/config/state (effective items + flags, redacted) ([7be1712](https://github.com/tekgnosis-net/LLM-Proxy/commit/7be171223eaf939c23d4a142073aea43c9a4ed9b))
* **ui:** GET/PUT /api/config/passthrough (raw advanced config) ([2b1e126](https://github.com/tekgnosis-net/LLM-Proxy/commit/2b1e126628df12a445968a6f7922b4d8f6294526))
* **ui:** item-model config store + /api/config/* helpers (v3) ([fece4f6](https://github.com/tekgnosis-net/LLM-Proxy/commit/fece4f6ab9ac0aed5d845207b62312888b656ff2))
* **ui:** Models screen on model items (flags + strikethrough delete + undo); catalog picker preserved ([a127581](https://github.com/tekgnosis-net/LLM-Proxy/commit/a12758162b35ffb7667f787f9e836eb1d3805831))
* **ui:** POST /api/apply + /api/discard + GET /api/config/rendered ([9ea81d5](https://github.com/tekgnosis-net/LLM-Proxy/commit/9ea81d5a6f5700fd82ffbf62c55425ad3f68a36c))
* **ui:** POST /api/discard — revert staged changes to last-applied baseline ([1c23258](https://github.com/tekgnosis-net/LLM-Proxy/commit/1c2325891d41096b2cd145c1e9fa2aeaf57fd0af))
* **ui:** ProviderKeys on credential items (flags + strikethrough delete + undo) ([a240ce5](https://github.com/tekgnosis-net/LLM-Proxy/commit/a240ce5df09ff81d8ad8a89141952f36c66857e9))
* **ui:** PUT/DELETE /api/config/item (stage; credential key encrypted) ([aa838df](https://github.com/tekgnosis-net/LLM-Proxy/commit/aa838df9f1a52f4ab50c72f95d5d2f4c14ea68e0))
* **ui:** Routing screen on router_setting items + flag indicators ([25725f8](https://github.com/tekgnosis-net/LLM-Proxy/commit/25725f8491deb91bc341b6e3536e1152ee6a68fb))
* **ui:** Settings passthrough editor + ConfigViewer rendered preview + Dashboard on items; frontend build green ([571370a](https://github.com/tekgnosis-net/LLM-Proxy/commit/571370a186101ef62a9faab8c082456194198cfb))

# [1.12.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.11.0...v1.12.0) (2026-06-07)


### Bug Fixes

* **ui:** chmod config backups 0600 (they mirror the secret-bearing config) ([db9fb91](https://github.com/tekgnosis-net/LLM-Proxy/commit/db9fb91ae6c4b39d4351e1e225ff509e514d9fd7))


### Features

* **ui:** reject deleting a credential still referenced by a model (409) ([9c82031](https://github.com/tekgnosis-net/LLM-Proxy/commit/9c8203165a56ba06397ae72a84a6e5c2e3517de0))

# [1.11.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.10.0...v1.11.0) (2026-06-07)


### Bug Fixes

* **ui:** ensure_schema in catalog get_model/get_providers (safe before first sync) ([0d2fa44](https://github.com/tekgnosis-net/LLM-Proxy/commit/0d2fa44432587b90608dff7943a9b30fb3fc5beb))


### Features

* **ui:** /api/catalog routes + scheduled sync (default weekly + boot) ([2181723](https://github.com/tekgnosis-net/LLM-Proxy/commit/2181723119d7e36df8e26dcaf8b9c52e64e14ecb))
* **ui:** catalog parse fns + Catalog (pricing/endpoints sync) ([e4257cf](https://github.com/tekgnosis-net/LLM-Proxy/commit/e4257cf07d367a367991a77ec801a9a7ca53536f))
* **ui:** Models catalog auto-fill + Settings catalog panel ([ac162b0](https://github.com/tekgnosis-net/LLM-Proxy/commit/ac162b0998695a7882d54f81e3820f0f04a81601))

# [1.10.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.9.0...v1.10.0) (2026-06-07)


### Bug Fixes

* **ui:** 0600 on baseline/restore files (they may hold secrets) + redaction test ([4f623c7](https://github.com/tekgnosis-net/LLM-Proxy/commit/4f623c7c33a141395a1bb092f6a6ced6de3cd81d))
* **ui:** require non-empty credentials secret (drop weak fallback) ([fed1feb](https://github.com/tekgnosis-net/LLM-Proxy/commit/fed1feb55de399cae9daeb440c10d6edfc71c805))


### Features

* **ui:** /api/credentials vault + materialize into config (GET redacts, PUT injects) ([c6d794a](https://github.com/tekgnosis-net/LLM-Proxy/commit/c6d794ada0056c866535933e38ceab20ae345255))
* **ui:** /api/models/test (pre-save) + /api/models/health (cached) ([d8c3c0b](https://github.com/tekgnosis-net/LLM-Proxy/commit/d8c3c0b2cb842b2994a9ffffc42ca3ffeac43514))
* **ui:** config.yaml secret-bearing (0600, gitignored, .example, credential_list exempt + redacted) ([327178a](https://github.com/tekgnosis-net/LLM-Proxy/commit/327178a0f4b5770ce018adf4d7c7d03160cd4b06))
* **ui:** credentials_store (encrypted ui vault + materialize) ([9d100bb](https://github.com/tekgnosis-net/LLM-Proxy/commit/9d100bb6459a274c1c777854490da74241cefbce))
* **ui:** enable cached background health checks in config bootstrap ([2c04259](https://github.com/tekgnosis-net/LLM-Proxy/commit/2c04259c205bb1a4f7d433061a95a84bc5e84b96))
* **ui:** Models v2 — credential/mode/costs/test-connection/health ([7b707ae](https://github.com/tekgnosis-net/LLM-Proxy/commit/7b707aeda7947b9e6fa28f98b4aa2d9d8e128955))
* **ui:** Provider Keys screen (UI-owned encrypted vault) ([74aeb62](https://github.com/tekgnosis-net/LLM-Proxy/commit/74aeb628bddf4e6a826e0f0519b749c8b7aa3282))

# [1.9.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.8.0...v1.9.0) (2026-06-07)


### Features

* **ui:** apply_config with baseline rollback ([8680178](https://github.com/tekgnosis-net/LLM-Proxy/commit/86801783915f927e30432daa4c2d00eb8eb524aa))
* **ui:** config baseline + pending_status (staged-save support) ([6f40e79](https://github.com/tekgnosis-net/LLM-Proxy/commit/6f40e7916ea20092c0c3d9b88f1e20db738c62c6))
* **ui:** dashboard rebuild with KPI cards ([99b1070](https://github.com/tekgnosis-net/LLM-Proxy/commit/99b1070db0914f87f34d4db7eb809befc71f1cc4))
* **ui:** expose redis host/port for caching display; ignore .applied.yaml ([ee47ab0](https://github.com/tekgnosis-net/LLM-Proxy/commit/ee47ab0d4d0624a5dc2ab8fe619e9e3cb131b7a9))
* **ui:** PUT=save-only, POST /api/apply, GET /api/apply/status ([6c4e5ea](https://github.com/tekgnosis-net/LLM-Proxy/commit/6c4e5eaa8b403f4a9ccf3d23b75b880ccd3fd833))
* **ui:** read-only caching status panel ([7d3a0f4](https://github.com/tekgnosis-net/LLM-Proxy/commit/7d3a0f4b6b456e0c8b55f8596441d483ac5ef061))
* **ui:** routing timeout/cooldown/allowed_fails/retry_after ([7c48d0d](https://github.com/tekgnosis-net/LLM-Proxy/commit/7c48d0d4be76ed3c216d1e988c6856d50882dea9))
* **ui:** staged-save store + global Apply bar ([3699c00](https://github.com/tekgnosis-net/LLM-Proxy/commit/3699c0093eebefd293e0f7e101011b38907f6299))

# [1.8.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.7.0...v1.8.0) (2026-06-07)


### Features

* add setup_env_helper.sh — interactive .env creator/updater ([dafeb69](https://github.com/tekgnosis-net/LLM-Proxy/commit/dafeb69d858f84a1545a709c9a3c5c6a242667aa))

# [1.7.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.6.0...v1.7.0) (2026-06-06)


### Bug Fixes

* **ui:** clean export error + wire HOUSEKEEPING_DELETE_EXPIRED_KEYS env ([be4aa5a](https://github.com/tekgnosis-net/LLM-Proxy/commit/be4aa5af6e42f3e36c08d998ffb66b2a4c1eb423))


### Features

* **ui:** /api/housekeeping (stats + maintenance) + opt-in cron ([fe4189b](https://github.com/tekgnosis-net/LLM-Proxy/commit/fe4189bb99774aafb432fe20429ed58110349a40))
* **ui:** Caching, Housekeeping, Settings (export/import + dark mode) + nav ([32d2c0c](https://github.com/tekgnosis-net/LLM-Proxy/commit/32d2c0c9e47b02f116abc7ea40ad997554d153ce))
* **ui:** db_admin (asyncpg stats + maintenance SQL) ([31d230b](https://github.com/tekgnosis-net/LLM-Proxy/commit/31d230bd0074b61a4ded226375fc0798edf8a9f4))
* **ui:** GET /api/config/export (download config.yaml) ([89f98bf](https://github.com/tekgnosis-net/LLM-Proxy/commit/89f98bf34fd0fa92bde8fa3ad7efe78d8fc45688))
* **ui:** housekeeping deps + settings + DB env ([326532a](https://github.com/tekgnosis-net/LLM-Proxy/commit/326532a689a10d01ea13ccb69d8e8de541d15fdb))

# [1.6.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.5.0...v1.6.0) (2026-06-06)


### Features

* **ui:** /api/usage (combined spend/activity, resilient) ([7464e2f](https://github.com/tekgnosis-net/LLM-Proxy/commit/7464e2feb72f78f3db6a89d1aa6df98721bd7c88))
* **ui:** spend_client (total/by-model/by-key/activity) ([6dbdf29](https://github.com/tekgnosis-net/LLM-Proxy/commit/6dbdf298455425dea6c2014d56f124ccdb42729b))
* **ui:** Usage & Spend screen (spend cards, by-model/key, activity) ([a767985](https://github.com/tekgnosis-net/LLM-Proxy/commit/a767985508565b04dd7349719e18b6eaad0faefc))

# [1.5.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.4.0...v1.5.0) (2026-06-06)


### Bug Fixes

* **ui:** defensively strip any plaintext key from key list (defense-in-depth) ([2ec395b](https://github.com/tekgnosis-net/LLM-Proxy/commit/2ec395ba8d6f68bc317a771107c2ecd030612cce))


### Features

* **ui:** /api/keys routes (list/create/delete, login-gated) ([85e9f1b](https://github.com/tekgnosis-net/LLM-Proxy/commit/85e9f1b933957445f820523f8a5f316da85c25a2))
* **ui:** keys_client (list/generate/delete via litellm key API) ([fc6f2be](https://github.com/tekgnosis-net/LLM-Proxy/commit/fc6f2bee7809c8d379703c157e0f04ed6cf3d3fd))
* **ui:** Virtual Keys screen (create/list/delete with budgets) ([1cf4add](https://github.com/tekgnosis-net/LLM-Proxy/commit/1cf4add9e3001df2ea70e8820d2f3c7f98eaf82d))

# [1.4.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.3.0...v1.4.0) (2026-06-06)


### Bug Fixes

* **ui:** keep Models form on rejected save; resync Routing after save ([19c2cab](https://github.com/tekgnosis-net/LLM-Proxy/commit/19c2cabdf42774b65d567e73913edab0b427fb28))


### Features

* **ui:** config store + provider presets + putConfig (full round-trip) ([97349a0](https://github.com/tekgnosis-net/LLM-Proxy/commit/97349a0bf73b24f9ec62277b2c5aef6e286e4694))
* **ui:** Models screen (provider-driven CRUD on safe-apply) ([481663e](https://github.com/tekgnosis-net/LLM-Proxy/commit/481663e3cdc5cd40df1bfe4c0e19e514a3dfb800))
* **ui:** Routing screen (strategy + retries + fallbacks on safe-apply) ([402550b](https://github.com/tekgnosis-net/LLM-Proxy/commit/402550bb9578409aa8ef0bd3d77c28a54e5a4b98))
* **ui:** wire Models + Routing into the sidebar nav ([203a158](https://github.com/tekgnosis-net/LLM-Proxy/commit/203a158b4a69bf02dc3758e0dbe00540b2e04064))

# [1.3.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.2.0...v1.3.0) (2026-06-06)


### Features

* **ui:** reject literal secrets in config (enforce os.environ/ refs) ([7cf866c](https://github.com/tekgnosis-net/LLM-Proxy/commit/7cf866c402e5b83521f263497d0322179f0ff7a3))

# [1.2.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.1.0...v1.2.0) (2026-06-06)


### Bug Fixes

* **ui:** write config.yaml world-readable (0644) so host can read it ([f9c6c74](https://github.com/tekgnosis-net/LLM-Proxy/commit/f9c6c742004cf33e0eb1155cb92e4ce9be0becb3))


### Features

* **ui:** PUT /api/config with safe-apply (422 invalid, 409 rolled-back) ([2612a2e](https://github.com/tekgnosis-net/LLM-Proxy/commit/2612a2ec9aa261c39527424667473ab7410a93cb))
* **ui:** safe_apply orchestration with auto-rollback ([e96b872](https://github.com/tekgnosis-net/LLM-Proxy/commit/e96b87255e38abcc8d3f75d7ae9a6e457f8b21db))

# [1.1.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.0.0...v1.1.0) (2026-06-06)


### Bug Fixes

* **ui:** microsecond backup timestamps (avoid same-second collision) ([0991005](https://github.com/tekgnosis-net/LLM-Proxy/commit/0991005c653d6b27d797349983ed501129ce3087))


### Features

* **ui:** atomic config write with backup + guardrail header ([98cd098](https://github.com/tekgnosis-net/LLM-Proxy/commit/98cd098e263cb4b6a04dc71c85a4e494488aeb9a))
* **ui:** config dir mount + reload settings; drop unused database_url ([9e7a3f4](https://github.com/tekgnosis-net/LLM-Proxy/commit/9e7a3f4063fda535f91e70a728ea0bdf229c21e2))
* **ui:** reloader (restart mode per spike) — trigger + verify health/models ([4b47065](https://github.com/tekgnosis-net/LLM-Proxy/commit/4b470654600ed7ecde0442290f307be6c8c97854))
* **ui:** require model_name + litellm_params.model; validate_config helper ([945b10d](https://github.com/tekgnosis-net/LLM-Proxy/commit/945b10db3d3ad158b528b79a57281e92e71258d9))

# 1.0.0 (2026-06-06)


### Bug Fixes

* **config:** make config.yaml a config-only bootstrap (store_model_in_db=false) ([04fa3f5](https://github.com/tekgnosis-net/LLM-Proxy/commit/04fa3f50ad006d370d709c95d3522a14027a3d8f))
* **ui:** genuine in-model ssl guardrail + robust malformed-config handling ([a5f4bc3](https://github.com/tekgnosis-net/LLM-Proxy/commit/a5f4bc35fcceb273670ac71eca7a862d1057b432)), closes [#10949](https://github.com/tekgnosis-net/LLM-Proxy/issues/10949)


### Features

* **ui:** argon2 password auth helpers ([38218a5](https://github.com/tekgnosis-net/LLM-Proxy/commit/38218a58cd19494b99269ac84b966d79219e796d))
* **ui:** config_store with ssl + routing-strategy guardrails ([d2ee959](https://github.com/tekgnosis-net/LLM-Proxy/commit/d2ee959fafd64527e475d36b6d7dbf6351e47e93))
* **ui:** Dockerfile + compose wiring (ui + scoped socket-proxy) ([85e8dec](https://github.com/tekgnosis-net/LLM-Proxy/commit/85e8dec31026f4b4825a19a513173e519714e574))
* **ui:** FastAPI app, auth/health/config routes, static mount ([148fbc6](https://github.com/tekgnosis-net/LLM-Proxy/commit/148fbc65afdfe27e8db88e5721183fcc60fc0eb5))
* **ui:** litellm health client ([3f7b973](https://github.com/tekgnosis-net/LLM-Proxy/commit/3f7b973510e482c87479c26a38a4f40bead297d1))
* **ui:** scaffold backend package + settings ([70f7e72](https://github.com/tekgnosis-net/LLM-Proxy/commit/70f7e72a8a0fba07685d11db84553597220dbb73))
* **ui:** Svelte shell — login, dashboard, config viewer ([17ec6b7](https://github.com/tekgnosis-net/LLM-Proxy/commit/17ec6b715740385460f28df2efaa142002c8765b))
