## [1.31.1](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.31.0...v1.31.1) (2026-07-22)


### Bug Fixes

* audio/image/completion modes were unselectable — map litellm's plural endpoint keys; custom providers offer all modes ([9edbe58](https://github.com/tekgnosis-net/LLM-Proxy/commit/9edbe587590684dacbb508f06ab8341b12254503))

# [1.31.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.30.0...v1.31.0) (2026-07-21)


### Bug Fixes

* fold model_group_alias names into G so referencing them isn't a false orphan ([162ef4d](https://github.com/tekgnosis-net/LLM-Proxy/commit/162ef4d60c1d6de56dfef9c2c9f509daaffe9400))
* mga fold honors deleted flag in key validator (3-consumer consistency) + separator-safe collision keys ([071ba7d](https://github.com/tekgnosis-net/LLM-Proxy/commit/071ba7d1b78c333738e25d9321df13e8a18646c0))
* **ui:** reachability collision rows keyed by fallback setting + show the setting name ([23a072a](https://github.com/tekgnosis-net/LLM-Proxy/commit/23a072ab16a5261704b6b7311e26f2988daccbce))


### Features

* GET /api/config/reachability — advisory collision + per-key over-reach report ([e5b3e57](https://github.com/tekgnosis-net/LLM-Proxy/commit/e5b3e57e711c9bc7791d02e43568c57e7b85be21))
* reachability engine — parse/strip mirror + collision audit + per-key over-reach ([877b339](https://github.com/tekgnosis-net/LLM-Proxy/commit/877b339bb28c7789c05241f2c92cac2c27ed5358))
* **ui:** advisory reachability panel + per-key can-also-reach note ([31ec4ef](https://github.com/tekgnosis-net/LLM-Proxy/commit/31ec4efcd9ee49e44ae3eb1c1a0cc6fe5ddddee8))

# [1.30.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.29.0...v1.30.0) (2026-07-13)


### Bug Fixes

* exempt LiteLLM special model tokens + harden key validator against malformed payloads ([2213388](https://github.com/tekgnosis-net/LLM-Proxy/commit/22133881757a6ea7aa020e30a8bf1cf5c310c908))
* integrity checker skips unhashable/malformed leaf values instead of raising ([d20d993](https://github.com/tekgnosis-net/LLM-Proxy/commit/d20d99330b4b4ce3c67f0dddc779ab4f68275ff9))
* map integrity Apply-gate error to HTTP 422 (not 500) ([8e5d1be](https://github.com/tekgnosis-net/LLM-Proxy/commit/8e5d1befe2387f88ccf8086e58c3a3fae223d4cd))
* **ui:** integrity badge matches drift-badge pill style; key orphan list; disable Fix during apply ([2dcc899](https://github.com/tekgnosis-net/LLM-Proxy/commit/2dcc899153f20fb026f40469b85cd8344614475a))


### Features

* integrity report endpoint + pre-commit Apply-gate for dangling router refs ([ea97073](https://github.com/tekgnosis-net/LLM-Proxy/commit/ea9707319e5219dd68704329326d1915b0a8a047))
* per-orphan integrity fix endpoint (dry-run preview; router stages, key hot) ([8668a5d](https://github.com/tekgnosis-net/LLM-Proxy/commit/8668a5d3a89fc451f96f3ec3503ed26e51147dd9))
* pure referential-integrity checker (orphan detection + trim helpers) ([cf1f376](https://github.com/tekgnosis-net/LLM-Proxy/commit/cf1f37606d5b1a646365e9fecab7a7cbf59662a9))
* reject virtual keys that reference unknown model groups (models/aliases) ([c842c74](https://github.com/tekgnosis-net/LLM-Proxy/commit/c842c7488c3f1e3e7781433302401acbdd3ea67f))
* **ui:** Routing integrity panel — lists dangling references with per-orphan Fix ([868654b](https://github.com/tekgnosis-net/LLM-Proxy/commit/868654bf2061b2f27577a71e0c6107bcecc84498))

# [1.29.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.28.1...v1.29.0) (2026-07-06)


### Bug Fixes

* **ui:** harden tx-detail panel — error-body handling + null-guarded token/tag fields ([04710e5](https://github.com/tekgnosis-net/LLM-Proxy/commit/04710e55fccc47372562313586104e8f0be03139))
* **ui:** tx-detail error guard must not misread a failed transaction's error field as a fetch failure ([2abe90d](https://github.com/tekgnosis-net/LLM-Proxy/commit/2abe90d02342f057c990fd994bfd81c12d74e637))


### Features

* **ui:** Recent|History activity feed with filters, percentile strip, load-more + expand-in-place tx detail ([f13d866](https://github.com/tekgnosis-net/LLM-Proxy/commit/f13d866a0dbe743f99ef23799a3bc61c39c8e247))
* usage activity endpoint (windowed, filtered, keyset-paged, stats) + per-tx detail endpoint ([cae3aee](https://github.com/tekgnosis-net/LLM-Proxy/commit/cae3aee94843977aa0560145d9c1e1686839b062))

## [1.28.1](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.28.0...v1.28.1) (2026-07-02)


### Bug Fixes

* **ui:** make per-key aliases actually work + fix alias deletion ([0fb0d1d](https://github.com/tekgnosis-net/LLM-Proxy/commit/0fb0d1daf784f6c39a5bcef075c44dad18ff338c)), closes [#25281](https://github.com/tekgnosis-net/LLM-Proxy/issues/25281)

# [1.28.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.27.0...v1.28.0) (2026-07-01)


### Bug Fixes

* **ui:** auto-expand Router Settings on edit when the key has aliases (they live there) ([d082d4d](https://github.com/tekgnosis-net/LLM-Proxy/commit/d082d4dc347c6eb2840556e42e5c4c246e810010))


### Features

* **ui:** aliases.js — per-key model-alias rows↔dict converter ([4e3eac5](https://github.com/tekgnosis-net/LLM-Proxy/commit/4e3eac53f10212d555a55ebe8e5e2cda18fb880d))
* **ui:** per-key Model aliases picker in Virtual Keys Router Settings ([ca4047b](https://github.com/tekgnosis-net/LLM-Proxy/commit/ca4047bbcbff6438c4bd9db554e6919d1c68bac1))

# [1.27.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.26.0...v1.27.0) (2026-06-22)


### Features

* **ui:** per-deployment Timeout (s) field on Models → Advanced (litellm_params.timeout) ([c626653](https://github.com/tekgnosis-net/LLM-Proxy/commit/c62665302144cbf177fd80ff4a35d7b88710000b))

# [1.26.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.25.0...v1.26.0) (2026-06-22)


### Features

* **ui:** per-key fallbacks picker (no JSON) + refresh docs to current state ([f5c2bae](https://github.com/tekgnosis-net/LLM-Proxy/commit/f5c2bae9683fc2f16151562db0b1abe0701a395e))

# [1.25.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.24.0...v1.25.0) (2026-06-22)


### Features

* **ui:** add app favicon (circuit-core proxy mark on the brand gradient tile) ([ecdab69](https://github.com/tekgnosis-net/LLM-Proxy/commit/ecdab698de07d77e82c014b20e1fa741bca2eefd)), closes [#0a84](https://github.com/tekgnosis-net/LLM-Proxy/issues/0a84) [#5e5ce6](https://github.com/tekgnosis-net/LLM-Proxy/issues/5e5ce6)

# [1.24.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.23.0...v1.24.0) (2026-06-22)


### Features

* SESSION_COOKIE_SECURE env to mark the session cookie Secure behind TLS ([cc53bba](https://github.com/tekgnosis-net/LLM-Proxy/commit/cc53bbacce338b82da2ac38b503cea6e35ef2c96))

# [1.23.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.22.0...v1.23.0) (2026-06-21)


### Bug Fixes

* guard live_by_id against id-less live models in converge_content (mirror _live_ids) ([05d3b59](https://github.com/tekgnosis-net/LLM-Proxy/commit/05d3b59d8de848b7757c92df54a96214aaf322bb))
* **ui:** render Usage & Logs timestamps in the browser's local timezone ([beefcc0](https://github.com/tekgnosis-net/LLM-Proxy/commit/beefcc0ba4e08787d3dece58e171d176709bf186))
* update_model uses PATCH /model/{id}/update (old POST drops model_info) ([5fa4b10](https://github.com/tekgnosis-net/LLM-Proxy/commit/5fa4b1067d552505a5f4aa784c2f5fe103acaee1))


### Features

* drift reports content_drifted; resync converges content (converge_content=True) ([7d59a9a](https://github.com/tekgnosis-net/LLM-Proxy/commit/7d59a9a1c442b4e8c613ffc80883341d0271cfb1))
* model_content — shared UI-managed model_info allowlist + content_diff ([9bf9dc5](https://github.com/tekgnosis-net/LLM-Proxy/commit/9bf9dc5c07ce068d6b194bb207619a419a0b8f59))
* reconcile_models converge_content flag (resync-only content convergence) ([c8fec46](https://github.com/tekgnosis-net/LLM-Proxy/commit/c8fec46df4e64ea6dbeae3c1bcec51fd1e30f2af))
* **ui:** drift badge + resync preview/result include content drift ([1f9c588](https://github.com/tekgnosis-net/LLM-Proxy/commit/1f9c58830b68c2f0eb4c99ddb0fe12a5995c9a01))

# [1.22.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.21.1...v1.22.0) (2026-06-21)


### Bug Fixes

* **ui:** render resync result banner outside the hybrid/drift block so errors always surface (Task 3 review) ([0a420d9](https://github.com/tekgnosis-net/LLM-Proxy/commit/0a420d9f6e33d2839c8a03495c5f559c1ec9c2e2))
* **ui:** resync result uses local state (store error/notice are read-only getters) (Task 3 review) ([f2e8749](https://github.com/tekgnosis-net/LLM-Proxy/commit/f2e8749d354ffffc619e5ea11d9ad5a05091a38e))


### Features

* GET /api/config/drift + build_desired duplicate-id reporting ([19ff71b](https://github.com/tekgnosis-net/LLM-Proxy/commit/19ff71b07f83dba49ce13275b18df3e0cc9bd3dc))
* POST /api/config/resync — on-demand presence convergence (hot, no restart) ([f51c46f](https://github.com/tekgnosis-net/LLM-Proxy/commit/f51c46fb59fdebb5bb46b2a531341489eacb4a5f))
* **ui:** drift badge + Resync-to-proxy (preview/confirm) on Models screen ([c5f8a1f](https://github.com/tekgnosis-net/LLM-Proxy/commit/c5f8a1f3aa8f9d5c63f16dc6cd1e08aba74995d3))

## [1.21.1](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.21.0...v1.21.1) (2026-06-21)


### Bug Fixes

* **reconcile:** idempotent /model/new (constraint→update) + guard force_ids against null credential ([e959176](https://github.com/tekgnosis-net/LLM-Proxy/commit/e959176600a2c0822af291d3cc63f24720211462))
* **reconcile:** key desired by model_info.id + translate changed/credential signals to id-space ([6611867](https://github.com/tekgnosis-net/LLM-Proxy/commit/66118671ba58166b3b846b487c6832a28c0ca4c4))

# [1.21.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.20.0...v1.21.0) (2026-06-20)


### Bug Fixes

* **config:** /api/config/rendered is hybrid-aware (settings-only preview matches written file) ([30e6d95](https://github.com/tekgnosis-net/LLM-Proxy/commit/30e6d95948c9ef63c8dfde74b730ba71b7032c03))
* **engine:** hybrid restart-verify uses public model_name (not UUID); guard missing models_client; cleanups (Task 5 review) ([9aa5938](https://github.com/tekgnosis-net/LLM-Proxy/commit/9aa59387554eebf628e379a886bf4f2abccc5eef))
* **ui:** hybrid-aware Apply result (surface models report + failures); mode-aware restart banner; confirm migrate (final-review) ([2058a5b](https://github.com/tekgnosis-net/LLM-Proxy/commit/2058a5b2d10487ca99be27ebe2295a5a5b48dd9f))


### Features

* declarative model reconcile (diff + inline-key apply) ([b566c26](https://github.com/tekgnosis-net/LLM-Proxy/commit/b566c26588cb02e8f4e1ef5ec2a3f2147e435ef4))
* **engine:** hybrid apply — split-render (models hot, settings restart) ([ed512bf](https://github.com/tekgnosis-net/LLM-Proxy/commit/ed512bfe421d06c1a252fdc77e6bae4b233f8af4))
* GET /api/config/export (ui_config.json, encrypted creds) + retarget Settings link ([cd7fe21](https://github.com/tekgnosis-net/LLM-Proxy/commit/cd7fe215798a3b057aeac1449024f0bf56760e1b))
* ModelsClient for LiteLLM /model/* hot-apply API ([d38b1ed](https://github.com/tekgnosis-net/LLM-Proxy/commit/d38b1ed61fb185da9d184fa5938bbb915c95995a))
* prepare-hot-apply migration (empty-then-fill) + Settings runbook ([f2bc8e3](https://github.com/tekgnosis-net/LLM-Proxy/commit/f2bc8e348deb68b4905e5f698925bcd65ebdb9ac))
* **render:** extract render_model_entry (key inlining) + hybrid render mode ([2a01676](https://github.com/tekgnosis-net/LLM-Proxy/commit/2a0167643da6ea971f25f814800101aac0714ae9))
* route Apply through hybrid when STORE_MODEL_IN_DB; wire compose env; honest apply banner ([aba8489](https://github.com/tekgnosis-net/LLM-Proxy/commit/aba8489a40c19a0648f71d07c859a3fae67be704))

# [1.20.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.19.2...v1.20.0) (2026-06-20)


### Bug Fixes

* **ui:** health_check_interval is a general_setting, not litellm_setting ([96d422a](https://github.com/tekgnosis-net/LLM-Proxy/commit/96d422aaa21bdcf8e271595243048d177cc5aa9d))
* **ui:** usage auto-refresh updates in place — no scroll reset (silent load) ([9f680f4](https://github.com/tekgnosis-net/LLM-Proxy/commit/9f680f42ee64150884c6f9d24c65300fc1183990))


### Features

* **ui:** editable global health_check_interval in Settings ([f217906](https://github.com/tekgnosis-net/LLM-Proxy/commit/f2179069e081967a4eef47481e3e6600805aab8e))
* **ui:** per-model 'Check now' on-demand health button ([f10e8a6](https://github.com/tekgnosis-net/LLM-Proxy/commit/f10e8a661c60fa549e75bea8d7a4ade09bbd8baa))
* **ui:** per-model health-check disable toggle (+ one-time global skip flag) ([2c6b087](https://github.com/tekgnosis-net/LLM-Proxy/commit/2c6b0872563a51dbe9fd6500841c92c428611b7e))

## [1.19.2](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.19.1...v1.19.2) (2026-06-20)


### Bug Fixes

* **ui:** block saving a custom/local model with no api_base (sanity gate) ([d041517](https://github.com/tekgnosis-net/LLM-Proxy/commit/d0415172350cfee13a71620f38a55a1068cdbde6))

## [1.19.1](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.19.0...v1.19.1) (2026-06-20)


### Bug Fixes

* **ui:** auto-open Advanced (api_base) for custom/local providers ([99b9f01](https://github.com/tekgnosis-net/LLM-Proxy/commit/99b9f01e980953fce6bae03709ccc9467edd1343))

# [1.19.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.18.1...v1.19.0) (2026-06-20)


### Bug Fixes

* **ui:** virtual-key model-access list shows model names, not deployment UUIDs ([6535dc8](https://github.com/tekgnosis-net/LLM-Proxy/commit/6535dc83714685ea290f448411ebd5928705fbe2))


### Features

* **ui:** /api/usage/summary returns full dashboard (KPIs, by-provider/model/key, timeseries) ([ee64a4e](https://github.com/tekgnosis-net/LLM-Proxy/commit/ee64a4e78e0c6a8d6bc21f3edd0f62044099c60a))
* **ui:** uPlot Chart.svelte wrapper ([a6452ad](https://github.com/tekgnosis-net/LLM-Proxy/commit/a6452ad134191c3fb847aecc340c3d7785fba167))
* **ui:** Usage dashboard — KPIs, charts, by-provider/model/key tabs, recent feed, 24h/hourly ([82c8b14](https://github.com/tekgnosis-net/LLM-Proxy/commit/82c8b14e757798149edb26a5a12063629031e4a5))

## [1.18.1](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.18.0...v1.18.1) (2026-06-18)


### Bug Fixes

* **ui:** reloader restart trigger gets its own 60s timeout — Apply no longer 500s mid-restart ([a10bbb3](https://github.com/tekgnosis-net/LLM-Proxy/commit/a10bbb3ecb851e92fea455567d451c426f43d522))

# [1.18.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.17.1...v1.18.0) (2026-06-10)


### Features

* **ui:** /api/keys/update -> LiteLLM /key/update (pass-through) ([f8f8391](https://github.com/tekgnosis-net/LLM-Proxy/commit/f8f83918801a370e44672562f719be1b8fae437c))
* **ui:** admin_auth — DB override hash + verify_and_hash (TDD) ([7974489](https://github.com/tekgnosis-net/LLM-Proxy/commit/797448988edca984b05ae987e076e7d6e55faa04))
* **ui:** edit a virtual key in place (router settings/budgets/limits); default new-key strategy to inherit-global ([114f63d](https://github.com/tekgnosis-net/LLM-Proxy/commit/114f63d7bcec614024f4a9ebf7cb21facd1f0260))
* **ui:** login resolves DB-or-env admin hash; POST /api/auth/change-password ([418cf6e](https://github.com/tekgnosis-net/LLM-Proxy/commit/418cf6ed4e143b42aef3ae27d06e337bcbe39ca9))
* **ui:** Settings — change admin password ([47c6732](https://github.com/tekgnosis-net/LLM-Proxy/commit/47c673245fc042509279e191af96081590e356af))
* **ui:** Usage remembers range + a saved auto-refresh interval (polls in place, pauses when hidden) ([b871bba](https://github.com/tekgnosis-net/LLM-Proxy/commit/b871bbaa12f229d9eed0e53943ff484754aa1b99))

## [1.17.1](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.17.0...v1.17.1) (2026-06-10)


### Bug Fixes

* **ui:** Usage was empty despite real data — asyncpg interval binding + silent catch ([e9b08ce](https://github.com/tekgnosis-net/LLM-Proxy/commit/e9b08ce47e299ca7af97f2ee8a5545f0c5681b14))

# [1.17.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.16.0...v1.17.0) (2026-06-10)


### Bug Fixes

* **ui:** map model health by deployment id (model_info.id), not public name — dots were always grey ([dcf698d](https://github.com/tekgnosis-net/LLM-Proxy/commit/dcf698d047a29d60524c3f391a5d701a050a7347))


### Features

* **ui:** /api/usage/summary — SQL spend-by-model/key + daily over a range ([339917d](https://github.com/tekgnosis-net/LLM-Proxy/commit/339917df266458c8775e911efc442d082b06f0c4))
* **ui:** Logs screen — live follow (SSE) + Debug-logging toggle (set_verbose) ([4d55e15](https://github.com/tekgnosis-net/LLM-Proxy/commit/4d55e158b275aefae311d4014ec0f653432cf3b1))
* **ui:** richer Usage — range selector, totals, by-model/by-key tables, daily bars ([3e361c9](https://github.com/tekgnosis-net/LLM-Proxy/commit/3e361c9e253189345d3df077bf9cd4287734a6de))
* **ui:** SSE /api/logs/stream — de-framed live LiteLLM logs via socket-proxy ([520546e](https://github.com/tekgnosis-net/LLM-Proxy/commit/520546ebc62503d290b22cba83566f12bdc82e6b))

# [1.16.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.15.0...v1.16.0) (2026-06-10)


### Features

* **ui:** credential stage reuses existing key when api_key blank (editable provider keys) ([e7496d5](https://github.com/tekgnosis-net/LLM-Proxy/commit/e7496d521fac3e31021f124a21c001426e058cf8))
* **ui:** edit a Provider Key in place (blank key keeps the current secret) ([98c16c6](https://github.com/tekgnosis-net/LLM-Proxy/commit/98c16c6ad2cd360042375823189102839c4adf58))
* **ui:** per-key router reliability knobs (retries/timeout/cooldown/allowed_fails/retry_after) ([3ec57a2](https://github.com/tekgnosis-net/LLM-Proxy/commit/3ec57a22d4b6134980640b5f10f226149deee1db))
* **ui:** warn before saving a model with no API key ([aeb2ecb](https://github.com/tekgnosis-net/LLM-Proxy/commit/aeb2ecb0b18ab3946377410c358fb734238ddf56))

# [1.15.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.14.1...v1.15.0) (2026-06-10)


### Features

* **config:** reject a model appearing in >1 routing group ([d7245bc](https://github.com/tekgnosis-net/LLM-Proxy/commit/d7245bc82f22a5e1cf4608f2ccb810982e0ca770))
* **config:** render model_info.id = item uuid (stable deployment id; lowest_cost mitigation) ([517159c](https://github.com/tekgnosis-net/LLM-Proxy/commit/517159cfa1612bf780c7e9857481b8dbfa6f4f82))
* **ui:** clearer model-health states; wire router_settings redis for routing state ([65277fd](https://github.com/tekgnosis-net/LLM-Proxy/commit/65277fd0dff463589bf506210d40c4c6f20ed309))
* **ui:** edit a model in place (re-stage under same uuid) ([839aa44](https://github.com/tekgnosis-net/LLM-Proxy/commit/839aa4480f99c7278f4842aeea2e505530bdb4d1))
* **ui:** per-key Router Settings (strategy + fallbacks) on key create ([f5e5d76](https://github.com/tekgnosis-net/LLM-Proxy/commit/f5e5d763baf9f3d1b3ce4281f5f0e37715580830))
* **ui:** routing groups editor (per-model-name strategy) ([a86e8ff](https://github.com/tekgnosis-net/LLM-Proxy/commit/a86e8fff69a0359115727424534d5dec315cbc0c))
* **ui:** Routing single Save changes (stages all edited fields) ([7fa27a5](https://github.com/tekgnosis-net/LLM-Proxy/commit/7fa27a53aaf584fb5cc95644f5d11e4c5f1f2a4f))

## [1.14.1](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.14.0...v1.14.1) (2026-06-09)


### Bug Fixes

* **ui:** secure-context-safe uuid + clipboard (Add-model Save dead on plain-HTTP LAN) ([3378f1b](https://github.com/tekgnosis-net/LLM-Proxy/commit/3378f1b56b58d59153810aafe9ef0f82026290c7))
* **ui:** serve index.html no-cache so new builds are picked up (hashed assets immutable) ([994a5a6](https://github.com/tekgnosis-net/LLM-Proxy/commit/994a5a62c52393743e2243d6ecb9a7717f5d7fef))

# [1.14.0](https://github.com/tekgnosis-net/LLM-Proxy/compare/v1.13.1...v1.14.0) (2026-06-09)


### Bug Fixes

* **config:** model items keyed by uuid, model_name in data (duplicate public names coexist) ([7a36758](https://github.com/tekgnosis-net/LLM-Proxy/commit/7a36758685ac688cf749d8acd58bf06a0744d9a5))


### Features

* **config:** idempotent migration rekeying legacy model items to uuid identities ([95e9e49](https://github.com/tekgnosis-net/LLM-Proxy/commit/95e9e493bb1f2b84ba5bcee8a975d10566f3acb5))
* **ui:** /api/cache/stats (valkey INFO) + /api/proxy-info; redis dep; UI redis/proxy env ([a00942e](https://github.com/tekgnosis-net/LLM-Proxy/commit/a00942e22e3f1c3a5cb73a2a04925b8337b77cf1))
* **ui:** live cache stats panel + Dashboard proxy URL card + LITELLM_PROXY_PORT/HOST wiring ([97051d9](https://github.com/tekgnosis-net/LLM-Proxy/commit/97051d9a352afc22bff9933ff236f37d1cb7fd81))
* **ui:** Models uuid rows (dup names ok) + provider <select> + descriptive modes + cost per 1M ([72ede43](https://github.com/tekgnosis-net/LLM-Proxy/commit/72ede43981688f5810d38f7d63424e548cfb8de6))

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
