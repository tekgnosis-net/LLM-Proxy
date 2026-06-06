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
