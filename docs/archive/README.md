# Archived docs — legacy built-in-UI workflow

These guides documented operating this stack through **LiteLLM's bundled admin
UI**. The project has since pivoted to a **purpose-built Apple-HIG admin UI**
(see [`../admin-ui.md`](../admin-ui.md) and
[`../superpowers/specs/2026-06-07-llm-proxy-ui-design.md`](../superpowers/specs/2026-06-07-llm-proxy-ui-design.md)),
because the bundled UI proved unreliable on this stack (SSL-cache bug #10949,
Router-Settings page can't save, `store_model_in_db` silently overriding
`config.yaml`).

They're kept for reference — the LiteLLM concepts they explain (model groups,
routing strategies, virtual keys, budgets, cost-based routing) are still
accurate; only the *UI you use to configure them* has changed.

| File | What it covers | Status |
|---|---|---|
| `user-guide.md` | Built-in UI walkthrough (endpoints, keys, routing, fallbacks) | superseded |
| `cost-routing-guide.md` | Cost-based routing mental model + built-in UI steps | concepts valid; UI steps superseded |
| `design.md` | Original stack design rationale (Postgres, Valkey, healthchecks, secrets) | foundational rationale still valid |
