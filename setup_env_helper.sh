#!/usr/bin/env bash
#
# setup_env_helper.sh — interactive .env creator/updater for the LLM-Proxy stack.
#
# Creates .env if missing, or updates it in place if it exists, writing every
# variable docker-compose.yml needs — each with an explanatory comment. Existing
# values are offered as defaults (press Enter to keep). Secrets can be
# auto-generated. The admin password is hashed (argon2) and the resulting hash is
# automatically $$-escaped (docker compose interpolates $ in .env, which would
# otherwise mangle the hash and cause a silent login failure). Any custom keys
# you've added are preserved.
#
# Usage:  ./setup_env_helper.sh
#
set -uo pipefail

ENV_FILE=".env"
cd "$(dirname "$0")" || exit 1   # run from the repo root regardless of CWD

# Managed keys (everything docker-compose.yml references).
MANAGED="LITELLM_MASTER_KEY LITELLM_SALT_KEY POSTGRES_USER POSTGRES_PASSWORD \
UI_PORT ADMIN_PASSWORD_HASH SESSION_SECRET HOUSEKEEPING_ENABLED \
HOUSEKEEPING_INTERVAL_HOURS HOUSEKEEPING_SPENDLOG_RETENTION_DAYS \
HOUSEKEEPING_DELETE_EXPIRED_KEYS LITELLM_PROXY_PORT LITELLM_PROXY_HOST"

# ---- helpers ---------------------------------------------------------------

c_bold=$'\033[1m'; c_dim=$'\033[2m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_off=$'\033[0m'
# All human-facing messages go to STDERR so they never pollute a $(...) capture;
# only intended return values are printed to STDOUT.
info()  { printf '%s\n' "$*" >&2; }
ok()    { printf '  %s✓%s %s\n' "$c_grn" "$c_off" "$*" >&2; }
warn()  { printf '  %s⚠ %s%s\n' "$c_ylw" "$*" "$c_off" >&2; }

need() { command -v "$1" >/dev/null 2>&1; }

gen_key()    { echo "sk-$(openssl rand -hex 32)"; }   # provider-style key
gen_secret() { openssl rand -hex 32; }                # generic random secret

# Read the literal current value of a key from the existing .env (preserves $$).
get_current() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | head -n1 || true; }

# Current value of a key, or a fallback default if unset/empty.
current_or() { local v; v="$(get_current "$1")"; printf '%s' "${v:-$2}"; }

# Plain prompt with a default. Echoes the chosen value.
ask() {
  local prompt="$1" def="${2:-}" ans
  if [ -n "$def" ]; then read -r -p "$prompt [$def]: " ans; printf '%s' "${ans:-$def}"
  else read -r -p "$prompt: " ans; printf '%s' "$ans"; fi
}

# Secret field: keep existing / regenerate / enter manually (value never echoed).
# Args: KEY  generator-function-name  LABEL
secret_field() {
  local key="$1" genfn="$2" label="$3" cur a v
  cur="$(get_current "$key")"
  if [ -n "$cur" ]; then
    read -r -p "  $label exists — [K]eep / [r]egenerate / [e]nter new? [K/r/e]: " a
    case "${a:-K}" in
      r|R) v="$($genfn)"; ok "regenerated" ;;
      e|E) read -r -s -p "    new value: " v; echo ;;
      *)   v="$cur" ;;
    esac
  else
    read -r -p "  $label — [G]enerate random / [e]nter manually? [G/e]: " a
    case "${a:-G}" in
      e|E) read -r -s -p "    value: " v; echo ;;
      *)   v="$($genfn)"; ok "generated" ;;
    esac
  fi
  printf '%s' "$v"
}

# Hash a plaintext admin password to an argon2 hash, then $$-escape it.
# Tries docker compose (builds the UI image if needed), then the local venv.
gen_admin_hash() {
  local pw="$1" raw=""
  if need docker; then
    raw="$(docker compose run --rm --no-deps -e GEN_PW="$pw" llm-proxy-ui \
            python -c "import os;from app.auth import hash_password;print(hash_password(os.environ['GEN_PW']))" \
            2>/dev/null | tr -d '\r' | grep -E '^\$argon2' | tail -n1)"
  fi
  if [ -z "$raw" ] && [ -x ui/.venv/bin/python ]; then
    raw="$(cd ui && GEN_PW="$pw" .venv/bin/python \
            -c "import os;from app.auth import hash_password;print(hash_password(os.environ['GEN_PW']))" 2>/dev/null)"
  fi
  [ -z "$raw" ] && return 1
  printf '%s' "$raw" | sed 's/[$]/$$/g'   # escape every $ as $$ for docker compose
}

# ---- preflight -------------------------------------------------------------

need openssl || { echo "openssl is required (for generating keys). Install it and re-run."; exit 1; }

info "${c_bold}LLM-Proxy .env setup${c_off}"
if [ -f "$ENV_FILE" ]; then
  info "${c_dim}Found an existing $ENV_FILE — it will be updated (Enter keeps current values).${c_off}"
else
  info "${c_dim}No $ENV_FILE yet — a new one will be created.${c_off}"
fi
echo

# ---- collect values --------------------------------------------------------

info "${c_bold}Postgres${c_off} (internal compose network only)"
POSTGRES_USER="$(ask "  POSTGRES_USER" "$(current_or POSTGRES_USER litellm)")"
POSTGRES_PASSWORD="$(secret_field POSTGRES_PASSWORD gen_secret "POSTGRES_PASSWORD")"
echo

info "${c_bold}LiteLLM keys${c_off}"
LITELLM_MASTER_KEY="$(secret_field LITELLM_MASTER_KEY gen_key "LITELLM_MASTER_KEY (gates the proxy admin API)")"
warn "LITELLM_SALT_KEY encrypts provider keys in Postgres — do NOT change it once keys are stored, and back it up."
LITELLM_SALT_KEY="$(secret_field LITELLM_SALT_KEY gen_key "LITELLM_SALT_KEY")"
echo

info "${c_bold}Admin UI${c_off}"
UI_PORT="$(ask "  UI_PORT (host port for the admin UI)" "$(current_or UI_PORT 8081)")"
SESSION_SECRET="$(secret_field SESSION_SECRET gen_secret "SESSION_SECRET (signs login cookies)")"

# Admin password -> argon2 hash (special handling).
ADMIN_PASSWORD_HASH=""
cur_hash="$(get_current ADMIN_PASSWORD_HASH)"
if [ -n "$cur_hash" ]; then
  read -r -p "  Admin password hash exists. Change the admin password? [y/N]: " chg
  case "${chg:-N}" in y|Y) : ;; *) ADMIN_PASSWORD_HASH="$cur_hash"; ok "keeping current admin password" ;; esac
fi
if [ -z "$ADMIN_PASSWORD_HASH" ]; then
  while :; do
    read -r -s -p "  Choose an admin password: " p1; echo
    read -r -s -p "  Confirm password:        " p2; echo
    [ -n "$p1" ] && [ "$p1" = "$p2" ] && break
    warn "passwords empty or did not match — try again"
  done
  info "  Hashing (may pull the UI image on first run)…"
  if ADMIN_PASSWORD_HASH="$(gen_admin_hash "$p1")"; then
    ok "argon2 hash generated and \$\$-escaped for compose"
  else
    warn "couldn't hash automatically (docker and ui/.venv both unavailable)."
    info  "    Generate it later, then re-run this script (or paste into .env):"
    info  "      docker compose run --rm --no-deps llm-proxy-ui \\"
    info  "        python -c \"from app.auth import hash_password; print(hash_password('YOUR_PW'))\" | sed 's/[\$]/\$\$/g'"
    ADMIN_PASSWORD_HASH=""
  fi
  unset p1 p2
fi
echo

info "${c_bold}DB housekeeping${c_off} (optional scheduled maintenance)"
hk_def="$(current_or HOUSEKEEPING_ENABLED false)"
read -r -p "  Enable the maintenance cron? [y/N]: " hk
case "${hk:-$([ "$hk_def" = true ] && echo y || echo n)}" in y|Y) HOUSEKEEPING_ENABLED=true ;; *) HOUSEKEEPING_ENABLED=false ;; esac
if [ "$HOUSEKEEPING_ENABLED" = true ]; then
  HOUSEKEEPING_INTERVAL_HOURS="$(ask "  Run every N hours" "$(current_or HOUSEKEEPING_INTERVAL_HOURS 24)")"
  HOUSEKEEPING_SPENDLOG_RETENTION_DAYS="$(ask "  Keep spend logs for N days" "$(current_or HOUSEKEEPING_SPENDLOG_RETENTION_DAYS 90)")"
  read -r -p "  Also delete expired virtual keys? [Y/n]: " dk
  case "${dk:-Y}" in n|N) HOUSEKEEPING_DELETE_EXPIRED_KEYS=false ;; *) HOUSEKEEPING_DELETE_EXPIRED_KEYS=true ;; esac
else
  HOUSEKEEPING_INTERVAL_HOURS="$(current_or HOUSEKEEPING_INTERVAL_HOURS 24)"
  HOUSEKEEPING_SPENDLOG_RETENTION_DAYS="$(current_or HOUSEKEEPING_SPENDLOG_RETENTION_DAYS 90)"
  HOUSEKEEPING_DELETE_EXPIRED_KEYS="$(current_or HOUSEKEEPING_DELETE_EXPIRED_KEYS true)"
fi
echo

info "${c_bold}Proxy endpoint${c_off} (advertised to OpenAI-compatible clients)"
detected_ip="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' || hostname -I 2>/dev/null | awk '{print $1}')"
LITELLM_PROXY_PORT="$(ask "  LITELLM_PROXY_PORT (host-facing port clients call)" "$(current_or LITELLM_PROXY_PORT 4000)")"
LITELLM_PROXY_HOST="$(ask "  LITELLM_PROXY_HOST (LAN IP/host; blank = UI auto-detects)" "$(current_or LITELLM_PROXY_HOST "${detected_ip}")")"
echo

# ---- preserve any non-managed custom keys ----------------------------------

EXTRAS=""
if [ -f "$ENV_FILE" ]; then
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue ;; esac
    key="${line%%=*}"
    case " $MANAGED " in *" $key "*) ;; *) EXTRAS="${EXTRAS}${line}"$'\n' ;; esac
  done < "$ENV_FILE"
fi

# ---- write .env (atomic: temp file + mv) -----------------------------------

tmp="$ENV_FILE.tmp.$$"
{
  echo "# ============================================================================"
  echo "# LLM-Proxy environment — managed by setup_env_helper.sh"
  echo "# Contains secrets. NOT committed (.gitignore'd). File mode 600."
  echo "# ============================================================================"
  echo
  echo "# Postgres credentials. Used only on the internal compose network."
  printf 'POSTGRES_USER=%s\n' "$POSTGRES_USER"
  printf 'POSTGRES_PASSWORD=%s\n\n' "$POSTGRES_PASSWORD"
  echo "# Master key — gates the proxy admin API and is the UI's server-side"
  echo "# credential to LiteLLM. Safe to rotate."
  printf 'LITELLM_MASTER_KEY=%s\n\n' "$LITELLM_MASTER_KEY"
  echo "# Salt key — encrypts provider API keys stored in Postgres."
  echo "# WARNING: do NOT rotate after adding provider keys (makes them"
  echo "# undecryptable, forcing re-entry of every key). Back this value up."
  printf 'LITELLM_SALT_KEY=%s\n\n' "$LITELLM_SALT_KEY"
  echo "# Host port for the admin UI (http://<host>:UI_PORT)."
  printf 'UI_PORT=%s\n\n' "$UI_PORT"
  echo "# Admin UI login — argon2 hash of your admin password."
  echo "# The \$ characters are escaped as \$\$ because docker compose interpolates"
  echo "# \$ in .env values; an un-escaped hash mangles to blank -> silent login fail."
  printf 'ADMIN_PASSWORD_HASH=%s\n\n' "$ADMIN_PASSWORD_HASH"
  echo "# Secret used to sign admin UI session cookies."
  printf 'SESSION_SECRET=%s\n\n' "$SESSION_SECRET"
  echo "# Host-facing port for the LiteLLM proxy (clients call this). Default 4000."
  printf 'LITELLM_PROXY_PORT=%s\n' "$LITELLM_PROXY_PORT"
  echo "# LAN IP / host clients use to reach the proxy. Leave blank to auto-detect"
  echo "# (the UI uses the host you opened it on)."
  printf 'LITELLM_PROXY_HOST=%s\n\n' "$LITELLM_PROXY_HOST"
  echo "# DB housekeeping — opt-in maintenance cron in the UI backend."
  echo "# When enabled: trims spend logs older than the retention window and"
  echo "# (optionally) deletes expired virtual keys, every N hours."
  printf 'HOUSEKEEPING_ENABLED=%s\n' "$HOUSEKEEPING_ENABLED"
  printf 'HOUSEKEEPING_INTERVAL_HOURS=%s\n' "$HOUSEKEEPING_INTERVAL_HOURS"
  printf 'HOUSEKEEPING_SPENDLOG_RETENTION_DAYS=%s\n' "$HOUSEKEEPING_SPENDLOG_RETENTION_DAYS"
  printf 'HOUSEKEEPING_DELETE_EXPIRED_KEYS=%s\n' "$HOUSEKEEPING_DELETE_EXPIRED_KEYS"
  if [ -n "$EXTRAS" ]; then
    echo
    echo "# --- other keys preserved from your previous .env ---"
    printf '%s' "$EXTRAS"
  fi
} > "$tmp"
mv "$tmp" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ---- summary ---------------------------------------------------------------

echo
ok "Wrote ${c_bold}$ENV_FILE${c_off} (mode 600)."
if [ -z "$ADMIN_PASSWORD_HASH" ]; then
  warn "ADMIN_PASSWORD_HASH is empty — set it before the UI login will work."
fi
info "Next:"
info "  ${c_dim}docker compose config -q   # validate (no 'variable not set' warnings)${c_off}"
info "  ${c_dim}docker compose up -d       # start the stack${c_off}"
info "  ${c_dim}open http://<host>:${UI_PORT} and log in${c_off}"
