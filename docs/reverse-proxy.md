# Running behind a reverse proxy (nginx / Apache / CloudPanel)

This stack runs fine behind a TLS-terminating reverse proxy. Two services are
typically exposed:

| Public host (example) | Upstream | Purpose |
|---|---|---|
| `proxy.example.com` | `127.0.0.1:8081` (the **admin UI**) | The web admin UI |
| `api.example.com` | `127.0.0.1:4000` (**LiteLLM `/v1`**) | The OpenAI-compatible endpoint your LLM clients call |

They have **different** proxy requirements — most importantly, you may put HTTP
Basic Auth (a "directory password") in front of the admin UI, but **never** in
front of `/v1` (clients authenticate with `Authorization: Bearer <key>`, which
Basic Auth would clobber).

There are only a handful of app-specific things to get right:

- **`/api/apply` is slow on purpose.** Applying config can restart the LiteLLM
  container and the UI waits up to ~90s for it to come back healthy
  (`reloader.py` `timeout_s=90`). The proxy's read timeout must exceed this or a
  *successful* Apply returns 504. Use **120s**.
- **`/api/logs/stream` is Server-Sent Events.** The Logs screen streams with
  `EventSource`. The proxy must not buffer it and must allow a long-lived
  connection. (The app already sends `X-Accel-Buffering: no` + `Cache-Control:
  no-cache` on that response, so nginx streams it even without extra config —
  the only thing you must add is a long read timeout.)
- **The UI is a hash-routed SPA** (`/#/models`). There are **no server-side deep
  routes**, so you do **not** need a `try_files` / SPA fallback — just proxy
  everything to the app.
- **The session cookie is `SameSite=Lax` and not `Secure` by default.** It works
  over HTTPS as-is. To add the `Secure` flag you have two options: set
  **`SESSION_COOKIE_SECURE=true`** in `.env` (app-native, proxy-independent — the
  recommended way), or have the proxy stamp it (`proxy_cookie_flags`, shown below)
  if you can't change env.

---

## Step 0 — bind the upstreams to localhost

So the only way in is through the TLS vhost, bind the published ports to
loopback in `docker-compose.yml` (or a `docker-compose.override.yml`):

```yaml
services:
  llm-proxy-ui:
    ports:
      - "127.0.0.1:${UI_PORT:-8081}:8080"   # was "${UI_PORT:-8081}:8080"
  litellm:
    ports:
      - "127.0.0.1:${LITELLM_PROXY_PORT:-4000}:4000"
```

Never expose `postgres`, `valkey`, or `socket-proxy` publicly — they have no
ports published by default; keep it that way.

---

## nginx

### Admin UI vhost (`proxy.example.com`)

```nginx
location / {
    proxy_pass http://127.0.0.1:8081;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Apply restarts the proxy and the UI waits on it (~90s). 60s would 504.
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;

    # Prefer SESSION_COOKIE_SECURE=true in .env instead. This proxy-side line is
    # only needed if you can't set env. nginx >= 1.19.3; cookie is named `session`.
    proxy_cookie_flags session secure samesite=lax;
}

# Live log stream — Server-Sent Events
location = /api/logs/stream {
    proxy_pass http://127.0.0.1:8081;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection        "";    # keep the upstream connection open
    proxy_buffering    off;                    # stream events
    proxy_cache        off;
    proxy_read_timeout 3600s;                  # long-lived; EventSource auto-reconnects
}
```

**Optional directory password** (extra layer in front of the app's own login):

```nginx
location / {
    auth_basic           "Restricted";
    auth_basic_user_file /etc/nginx/.htpasswd-llmproxy;   # htpasswd -c <file> <user>
    # ... the proxy_pass block from above ...
}
```

### LiteLLM `/v1` vhost (`api.example.com`)

```nginx
location / {
    proxy_pass http://127.0.0.1:4000;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection        "";

    proxy_buffering    off;        # stream tokens for `stream: true` completions
    proxy_read_timeout 600s;       # long generations / slow upstreams
    client_max_body_size 50m;      # large prompts / vision payloads

    # DO NOT add auth_basic here — clients authenticate with Authorization: Bearer,
    # and Basic Auth uses the same header.
}
```

---

## Apache (`mod_proxy` + `mod_proxy_http` + `mod_headers`)

Enable: `a2enmod proxy proxy_http headers`.

### Admin UI vhost

```apache
<VirtualHost *:443>
    ServerName proxy.example.com
    # ... SSLEngine / cert directives (CloudPanel/Certbot manage these) ...

    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"

    # SSE log stream: stream, don't buffer
    <Location "/api/logs/stream">
        ProxyPass         "http://127.0.0.1:8081/api/logs/stream" flushpackets=on
        ProxyPassReverse  "http://127.0.0.1:8081/api/logs/stream"
        SetEnv proxy-sendchunked 1
    </Location>

    # Everything else; timeout must exceed the ~90s Apply wait
    ProxyPass        "/" "http://127.0.0.1:8081/" timeout=120 retry=0
    ProxyPassReverse "/" "http://127.0.0.1:8081/"

    # Add the Secure flag to the session cookie (no app change)
    Header edit Set-Cookie ^(session=.*)$ "$1; Secure"

    # Optional directory password:
    # <Location "/">
    #   AuthType Basic
    #   AuthName "Restricted"
    #   AuthUserFile /etc/apache2/.htpasswd-llmproxy
    #   Require valid-user
    # </Location>
</VirtualHost>
```

`ProxyTimeout 120` (server or vhost scope) is an alternative to the per-rule
`timeout=120`.

### LiteLLM `/v1` vhost

```apache
<VirtualHost *:443>
    ServerName api.example.com
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"

    ProxyPass        "/" "http://127.0.0.1:4000/" flushpackets=on timeout=600 retry=0
    ProxyPassReverse "/" "http://127.0.0.1:4000/"
    LimitRequestBody 52428800       # ~50MB

    # NO Basic Auth here — clients use Authorization: Bearer.
</VirtualHost>
```

---

## CloudPanel specifics

CloudPanel manages the `server {}` wrapper and Let's Encrypt for you; you supply
the `location` blocks.

1. **Create two sites** (subdomains), one per upstream — e.g. a "Reverse Proxy"
   site for `proxy.example.com` → `127.0.0.1:8081` and another for
   `api.example.com` → `127.0.0.1:4000`. Issue a certificate for each under the
   site's **SSL/TLS** tab.
2. **Edit the Vhost** (Sites → site → **Vhost**) and paste the per-location
   tweaks above (Apply timeout, the SSE location, `proxy_cookie_flags`, body
   size). CloudPanel's nginx is recent enough for `proxy_cookie_flags`.
3. **Directory password:** use CloudPanel's **Basic Auth** feature on the admin
   UI site only. Leave it off the `api.example.com` site.
4. CloudPanel redirects HTTP→HTTPS by default — keep that on.

---

## Tell the UI its public API address

The Dashboard's **Proxy endpoint** card shows clients the base URL to point their
OpenAI SDK at. It reads `LITELLM_PROXY_HOST` / `LITELLM_PROXY_PORT` and falls
back to the browser's current hostname. When `/v1` lives on a different host than
the UI, set these in `.env` so the card advertises the real endpoint:

```bash
LITELLM_PROXY_HOST=api.example.com
LITELLM_PROXY_PORT=443
```

(These only affect what the card *displays*; they don't change routing.)

---

## Security hardening checklist

- [ ] Upstream ports bound to `127.0.0.1` (Step 0) — no direct, non-TLS access.
- [ ] HTTPS enforced (HTTP→HTTPS redirect on).
- [ ] `Secure` flag on the session cookie — `SESSION_COOKIE_SECURE=true` in `.env`
      (preferred), or `proxy_cookie_flags` / `Header edit` at the proxy.
- [ ] Basic Auth on the **admin UI** vhost; **never** on `/v1`.
- [ ] Strong admin password (`ADMIN_PASSWORD_HASH`, argon2 — see
      [`admin-ui.md`](admin-ui.md) for the `$$` escaping rule).
- [ ] `postgres` / `valkey` / `socket-proxy` not published publicly.
- [ ] Master key stays server-side (it already does — the browser never sees it).

## Bonus: HTTPS fixes the secure-context shims

Served over HTTPS, the UI runs in a *secure browser context*, so
`crypto.randomUUID()` and `navigator.clipboard` work natively — the plain-HTTP
LAN fallbacks in `ui/lib/browser.js` are no longer exercised. The "Copy" buttons
and key/UUID generation are more reliable behind the proxy than on a bare
`http://<lan-ip>` origin.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| **Apply shows an error / 504 after ~60s, but the proxy did restart** | Proxy read timeout too low. Raise to `120s` (`proxy_read_timeout` / `ProxyTimeout`). |
| **Logs screen shows "Waiting for log lines…" forever** | The SSE stream is being buffered. Ensure the `/api/logs/stream` location has `proxy_buffering off` (nginx) / `flushpackets=on` (Apache) and a long read timeout. |
| **Login redirect loop / immediately logged out** | Cookie not making it back. Don't strip cookies; if you forced `Secure` make sure the site is actually HTTPS end-to-end. |
| **`/v1` clients get 401 from the proxy, not LiteLLM** | Basic Auth is enabled on the `/v1` vhost — remove it; it conflicts with `Authorization: Bearer`. |
| **Streamed completions arrive all-at-once** | `/v1` vhost is buffering — set `proxy_buffering off` / `flushpackets=on`. |
| **Dashboard "Proxy endpoint" card shows the wrong URL** | Set `LITELLM_PROXY_HOST` / `LITELLM_PROXY_PORT` to the public `/v1` host. |
