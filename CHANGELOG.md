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
