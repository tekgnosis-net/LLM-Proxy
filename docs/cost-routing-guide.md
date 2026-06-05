# Cost-Based Routing & Access Control — A Mental Model Guide

This guide exists to answer one question:

> *How do virtual keys, model access groups, cost/accounting, and routing
> actually fit together — and how do I wire up cost-based routing from the
> UI?*

If you've read [`user-guide.md`](user-guide.md) and the pieces still feel
disconnected, start here. We build the mental model first, then do a
complete hands-on UI walkthrough.

---

## TL;DR — the whole system in one picture

There are **two independent axes** in LiteLLM. They are wired up
separately and only meet at the moment a request arrives.

```
  AXIS A — ACCESS & ACCOUNTING            AXIS B — ROUTING & COSTING
  "who may ask, and how much"             "which backend serves it, at what cost"

   Virtual Key                             Public Model Name  (e.g. "cheap")
      │ belongs to                              │ is served by
      ▼                                         ▼
   Team / User                            Model Group  (many deployments,
      │ carries                                 │        same public name)
      ▼                                         ▼
   ┌─────────────┬──────────────┐         Routing Strategy  (lowest-cost)
   │ Access      │ Budget       │              │ picks ONE deployment
   │ (which      │ (how much    │              ▼
   │  models)    │  $ allowed)  │         Concrete deployment runs the call
   └─────────────┴──────────────┘              │
                                               ▼
                                          Cost computed from the COST MAP
                                               │
            ◄──────────────────────────────────┘
            the cost is charged back to the key/team/user budget
```

- **Axis A** decides *whether the request is allowed* and *whose budget it
  spends*.
- **Axis B** decides *which physical backend serves it* and *what it costs*.
- They share **one number**: the per-request cost. The `lowest-cost`
  router *minimizes* it; the accounting system *charges* it. Same number,
  two consumers. **That's the link.**

Everything below expands this picture.

---

## The four pillars, each with exactly one job

| Pillar | Its one job | Where you set it |
|--------|-------------|------------------|
| **Virtual Key** | Authenticates a client and is the thing spend is attributed to. | **Virtual Keys** page |
| **Model Access Group** | A *label* that bundles several models so you can grant access to all of them at once. | **Models** page, per model (`access_groups`) |
| **Cost / Accounting** | Measures what each request cost and enforces budgets. | Automatic (cost map) + **Budgets** on keys/teams/users |
| **Routing** | Picks *which* deployment serves a request among equivalent ones. | **Settings → Router Settings** |

Keep these jobs separate in your head and the whole thing stops being
confusing. A key does **not** do routing. Routing does **not** do access
control. Budgets do **not** care which deployment was chosen — only what it
cost.

---

## The terminology trap: "model group" vs "model access group"

This is the #1 thing that makes LiteLLM confusing. Two similarly-named
concepts that do **completely different jobs**:

| | **Model group** | **Model access group** |
|---|---|---|
| **Also called** | "deployments under one model_name" | `access_groups` |
| **What it is** | Several *deployments* that share the same **Public Model Name** | A *label* attached to one or more models |
| **Shape** | Many backends → **one** name | One label → **many** names |
| **Whose tool is it** | The **router** | **Virtual keys / teams** (access control) |
| **Job** | Load-balance & cost-route *equivalent* backends | Grant a key/team access to a *bundle* of models at once |
| **Where defined** | Implicitly — add ≥2 models with the same Public Model Name | Explicitly — set `access_groups` on a model |
| **Example** | `cheap` = gpt-4o-mini **and** claude-haiku **and** gemini-flash | `tier-cheap` label on `cheap`, `mid`, `summarize` |

Read those two example rows carefully — they are inverses of each other:

- A **model group** collapses *many backends into one name* so the router
  can choose among them.
- A **model access group** expands *one label into many names* so a key
  can be granted all of them in one click.

A single deployment can participate in **both**: it has a Public Model
Name (joining a model group) and it can carry one or more `access_groups`
labels (joining access groups). They never interfere.

---

## How a request flows (where each pillar acts)

Follow one chat request through the proxy. The pillar acting at each step
is in **bold**.

1. Client sends `POST /v1/chat/completions` with
   `Authorization: Bearer sk-...` and `"model": "cheap"`.

2. **[Virtual Key]** LiteLLM looks up the key. Valid? Not expired? Not over
   budget? It reads the key's **allowed models** — which may be literal
   names (`cheap`, `mid`) or **access-group** labels (`tier-cheap`).

3. **[Model Access Group]** If the key was granted `tier-cheap`, LiteLLM
   expands that label to the set of Public Model Names tagged with it, and
   checks that the requested `cheap` is in the allowed set. If not → HTTP
   401, request never runs.

4. **[Routing]** The request is allowed. `cheap` is a **model group** with
   three deployments (gpt-4o-mini, claude-haiku, gemini-flash). The
   **routing strategy** (`lowest-cost`) consults the cost map and picks the
   cheapest *healthy* deployment for this request.

5. The chosen deployment calls the real provider. Response comes back.

6. **[Cost / Accounting]** LiteLLM computes
   `cost = input_tokens × input_price + output_tokens × output_price`
   using the **same cost map** the router just used. It writes a row to
   `LiteLLM_SpendLogs` and **increments the spend** on: this key, its user,
   its team, and the global total.

7. **[Cost / Accounting]** On the *next* request, step 2's budget check
   uses the now-higher spend. When spend crosses `max_budget`, the key (or
   its team) starts returning 401 until the budget window resets.

Notice routing (step 4) and accounting (step 6) both used the cost map.
That shared number is the entire reason "cost-based routing" also means
"slower budget burn."

---

## The accounting hierarchy (who pays, who gets blocked)

Spend rolls **up** through four levels. A budget can be set at any level,
and **the most restrictive applicable budget wins.**

```
        Global budget            (optional, whole proxy)
            ▲ spend rolls up
        Team budget              (shared by all the team's keys)
            ▲
        User budget              (one person, may span teams)
            ▲
        Key budget               (one virtual key)
            ▲
        each request's cost
```

- A request's cost is added to **all** levels at once.
- Before a request runs, **every** level that has a budget is checked. If
  *any* is exhausted, the request is blocked — even if the others have
  headroom.

**Worked example.** Team `experiments` has `max_budget = $20/30d`. It has
two keys, each with `max_budget = $15`. The keys can spend $15 each *only
if the team hasn't hit $20 first*. If key A spends $15 and key B spends $5,
the team is at $20 and **both keys are now blocked**, even though key B had
$10 of its own headroom left. The team cap is the binding constraint.

### Budget fields you'll set in the UI

| Field | Meaning |
|-------|---------|
| **Max Budget (USD)** | Hard cap. Spend past it → 401. |
| **Budget Duration** | Reset window: `30d`, `7d`, `24h`, `1mo`. Omit for a lifetime cap that never resets. |
| **Soft Budget** | Alert threshold. Fires a warning (logs/webhook) but does **not** block. Use it as an early warning before the hard cap. |
| **TPM / RPM limits** | Throughput caps (tokens/requests per minute). Orthogonal to cost — they limit *rate*, not *money*. |

---

## Where "cost" comes from

LiteLLM ships a cost map: `model_prices_and_context_window.json`, bundled
in the image and refreshed with each LiteLLM release. It holds
`input_cost_per_token` and `output_cost_per_token` for thousands of known
models.

- For **known** models (gpt-4o-mini, claude-haiku, gemini-flash, …) you get
  accurate costs for free — both routing and accounting just work.
- For **unknown / custom-priced** models (self-hosted, negotiated pricing,
  a brand-new model not yet in the map) the cost is `0` unless you set it.
  A `lowest-cost` router will then think that model is *free* and send it
  everything. **Always set explicit prices on custom models** (see the
  walkthrough's optional step).

> Keep the image reasonably current (`docker compose pull litellm`) so the
> cost map reflects recent provider price changes.

---

## A critical limitation of `lowest-cost` (read this twice)

**`lowest-cost` only chooses among the deployments inside ONE model group
— i.e. deployments sharing the same Public Model Name.**

It does **not** look across different Public Model Names and pick a cheaper
one for you. If a client asks for `gpt-4o`, the router will *not* silently
downgrade it to `gpt-4o-mini` — those are two different model groups.

So there are two ways to actually get cost savings:

1. **Co-locate providers under one name** (the simple, UI-only way). Put
   several comparable models under a single Public Model Name so they form
   one model group, then `lowest-cost` picks the cheapest each time:

   | Public Model Name | Deployments in the group |
   |-------------------|--------------------------|
   | `cheap` | gpt-4o-mini, claude-haiku, gemini-flash |

   The client always asks for `cheap`; the router picks whichever of the
   three is cheapest for that request. This is genuine cost optimization
   *within a quality tier*.

2. **Use a smart router for cross-tier savings** (`adaptive_router` /
   `complexity_router`, config.yaml only). These *will* route a simple
   prompt to a cheaper model and a hard prompt to a stronger one. Covered
   in [`user-guide.md` §5](user-guide.md). Out of scope for the UI-only
   path here.

The walkthrough below uses approach #1 — fully doable from the UI.

---

## Hands-on: build a cost-optimized, access-controlled setup in the UI

Goal: three quality tiers, each load-balanced + cost-optimized across
providers, gated by access groups, with team budgets that auto-reset
monthly. Everything except the smart-router note is pure UI.

### Step 1 — Add deployments, grouped by Public Model Name, tagged with access groups

Sidebar → **Models → Add Model**. Add each row below. The two columns that
do the conceptual work are **Public Model Name** (forms the model group)
and **Model Access Group** (the access label).

| Public Model Name | Provider | LiteLLM Model | Model Access Group |
|-------------------|----------|---------------|--------------------|
| `cheap` | OpenAI | `openai/gpt-4o-mini` | `tier-cheap` |
| `cheap` | Anthropic | `anthropic/claude-haiku-4-5-20251001` | `tier-cheap` |
| `cheap` | Google | `gemini/gemini-2.0-flash` | `tier-cheap` |
| `mid` | OpenAI | `openai/gpt-4o` | `tier-mid` |
| `mid` | Anthropic | `anthropic/claude-3-5-sonnet-20241022` | `tier-mid` |
| `premium` | OpenAI | `openai/o1-preview` | `tier-premium` |
| `premium` | Anthropic | `anthropic/claude-opus-4-7` | `tier-premium` |

What just happened, in terms of the two axes:

- **Routing axis:** rows sharing a Public Model Name (`cheap` ×3, `mid` ×2,
  `premium` ×2) automatically became three **model groups**. The router
  can now load-balance and cost-route within each.
- **Access axis:** the `access_groups` labels created three **model access
  groups** (`tier-cheap`, `tier-mid`, `tier-premium`) you can grant in one
  click.

> **Where the access-group field is in the UI:** on the Add Model form it's
> usually labelled **"Model Access Group"** (sometimes under *Advanced
> Settings*). You can type a new label there to create it, or pick an
> existing one. In `config.yaml` the equivalent is:
> ```yaml
> - model_name: cheap
>   litellm_params:
>     model: openai/gpt-4o-mini
>     api_key: os.environ/OPENAI_API_KEY
>   model_info:
>     access_groups: ["tier-cheap"]
> ```

### Step 1b (optional but recommended) — set explicit prices on any non-standard model

For any model **not** in LiteLLM's cost map (self-hosted, custom pricing),
edit the model row → **Advanced Settings → Model Info** and set
**Input Cost per Token** and **Output Cost per Token** (USD per *token*, so
$0.15 / 1M tokens = `0.00000015`). Skip this for standard hosted models —
the cost map already knows them. Without it, `lowest-cost` treats an
unpriced model as free and floods it.

### Step 2 — Turn on cost-based routing

Sidebar → **Settings → Router Settings → Routing Strategy** → choose
**`lowest-cost`** → **Save**. Effective immediately, no restart.

Now every call to `cheap` / `mid` / `premium` picks the cheapest *healthy*
deployment in that group per request.

> Equivalent in `config.yaml` (the UI value wins if both are set):
> ```yaml
> router_settings:
>   routing_strategy: lowest-cost
> ```

### Step 3 — Create a team with a budget and an access grant

Sidebar → **Teams → + Create New Team**.

| Field | Value |
|-------|-------|
| Team Name | `experiments` |
| Models | `tier-cheap`, `tier-mid` ← **pick the access-group labels here** |
| Max Budget (USD) | `20` |
| Budget Duration | `30d` |

The **Models** multiselect is where the two axes connect: you select the
**access-group labels** (`tier-cheap`, `tier-mid`), and LiteLLM expands
them to every Public Model Name tagged with those labels. This team can now
reach `cheap` and `mid` — but **not** `premium`, because you didn't grant
`tier-premium`.

> You *could* instead list individual model names (`cheap`, `mid`) here and
> skip access groups entirely. Access groups pay off when you have many
> models and many keys: tag once, grant the label, and adding a new model
> to the tier later automatically extends access to everyone who has the
> label — no per-key edits.

### Step 4 — Issue a virtual key under the team

Sidebar → **Virtual Keys → + Create New Key**.

| Field | Value |
|-------|-------|
| Key Alias | `experiments-laptop` |
| Team | `experiments` |
| Models | *(leave empty to inherit the team's access)* |
| Max Budget (USD) | `15` *(optional per-key sub-cap under the $20 team cap)* |
| Budget Duration | `30d` |
| Expiry | `90d` *(optional auto-revoke)* |

Copy the `sk-…` shown **once**. This key:

- May call only `cheap` and `mid` (inherited from the team's access
  groups).
- Spends against both its own $15 cap **and** the team's shared $20 cap —
  whichever binds first.

### Step 5 — Use it and watch the accounting

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-EXPERIMENTS-LAPTOP-KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"cheap","messages":[{"role":"user","content":"Summarize the plot of Hamlet in two sentences."}]}'
```

Then look at the result of routing + accounting:

- Sidebar → **Logs** → open the request. You'll see *which* deployment
  `cheap` resolved to and the cost line, e.g.
  *"lowest-cost: picked `gemini/gemini-2.0-flash` ($0.000018)."*
- Sidebar → **Usage** → spend now shows against `experiments-laptop`
  (the key) and `experiments` (the team).
- Try `"model":"premium"` with this key → **401**, because the team was
  never granted `tier-premium`. That's the access axis doing its job.

---

## Putting the whole thing in one config (for reference / disaster recovery)

You can manage all of the above from the UI; this is what the equivalent
`config.yaml` looks like, useful as documentation and for rebuilding from
scratch. UI-added state in Postgres overrides this on boot.

```yaml
# config/config.yaml
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
  store_model_in_db: true

router_settings:
  routing_strategy: lowest-cost
  cooldown_time: 30        # seconds a failed deployment sits out
  num_retries: 2

litellm_settings:
  # Cross-tier fallback if an entire group is down (see user-guide.md §4)
  fallbacks:
    - cheap: ["mid"]
    - mid: ["premium"]

model_list:
  - model_name: cheap
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info: { access_groups: ["tier-cheap"] }
  - model_name: cheap
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001
      api_key: os.environ/ANTHROPIC_API_KEY
    model_info: { access_groups: ["tier-cheap"] }
  - model_name: cheap
    litellm_params:
      model: gemini/gemini-2.0-flash
      api_key: os.environ/GEMINI_API_KEY
    model_info: { access_groups: ["tier-cheap"] }
  - model_name: mid
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY
    model_info: { access_groups: ["tier-mid"] }
  - model_name: mid
    litellm_params:
      model: anthropic/claude-3-5-sonnet-20241022
      api_key: os.environ/ANTHROPIC_API_KEY
    model_info: { access_groups: ["tier-mid"] }
  - model_name: premium
    litellm_params:
      model: openai/o1-preview
      api_key: os.environ/OPENAI_API_KEY
    model_info: { access_groups: ["tier-premium"] }
  - model_name: premium
    litellm_params:
      model: anthropic/claude-opus-4-7
      api_key: os.environ/ANTHROPIC_API_KEY
    model_info: { access_groups: ["tier-premium"] }
```

Note that **teams, keys, and budgets are *not* in this file** — those live
only in Postgres and are managed from the UI (or the `/team/new`,
`/key/generate` APIs). `config.yaml` defines the model catalog and routing
policy; the database defines who can use it and for how much.

---

## Mental-model recap

- **Two axes, set up separately, meet at request time.** Access/accounting
  (keys, teams, budgets, access groups) decide *whether* and *whose
  budget*. Routing/costing (model groups, strategy, cost map) decide
  *which backend* and *what cost*.
- **"Model group" ≠ "model access group."** Many-backends-one-name (for the
  router) vs. one-label-many-names (for access). Orthogonal; a model can be
  in both.
- **One cost number, two consumers.** `lowest-cost` minimizes it; the
  budget system charges it. That shared number is why cost routing extends
  your budget.
- **`lowest-cost` only optimizes *within* a model group.** Co-locate
  comparable providers under one Public Model Name to get savings; use a
  smart router for cross-tier.
- **Budgets stack and the tightest wins.** Key under user under team under
  global; any exhausted level blocks the request.

For the broader UI tour (other providers, fallbacks, debugging) see
[`user-guide.md`](user-guide.md). For why the stack is built the way it is,
see [`design.md`](design.md).
