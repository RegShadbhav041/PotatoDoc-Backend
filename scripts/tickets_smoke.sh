#!/usr/bin/env bash
# End-to-end smoke test for the support-ticket chat (farmer <-> superadmin).
# Usage: scripts/tickets_smoke.sh [base-url] [admin-contact] [admin-password]
#   (defaults: http://127.0.0.1:8000; the superadmin half is skipped unless
#    both admin credentials are given, e.g. the POTATO_SUPERADMIN_* pair.)
set -uo pipefail

BASE="${1:-http://127.0.0.1:8000}"
ADMIN_CONTACT="${2:-}"
ADMIN_PASSWORD="${3:-}"
CONTACT="tksmoke-$(date +%s)-${RANDOM}@example.com"
PASS="potato1234"
FAILURES=0

pass()  { printf '  %-52s OK\n' "$1"; }
fail()  { printf '  %-52s FAIL\n' "$1"; FAILURES=$((FAILURES + 1)); }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (got: ${2:-<empty>})"; fi; }

# str_field '<json>' '["token"]'  -> extracted value, or empty on any error
str_field() {
  printf '%s' "$1" | python3 -c 'import sys,json;print(json.load(sys.stdin)'"$2"')' 2>/dev/null || true
}

echo "Smoke-testing ticket chat on $BASE"

# --- guards ------------------------------------------------------------------
assert_eq "GET /tickets rejects anonymous callers" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/tickets")" "401"
assert_eq "GET /admin/tickets rejects anonymous callers" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/admin/tickets")" "401"

# --- farmer opens a ticket ---------------------------------------------------
REG=$(curl -s -X POST "$BASE/auth/register" -H 'Content-Type: application/json' \
  -d "{\"contact\":\"$CONTACT\",\"name\":\"Ticket Farmer\",\"password\":\"$PASS\"}")
TOKEN=$(str_field "$REG" '["token"]')
assert_eq "POST /auth/register returns a token" "$([ -n "$TOKEN" ] && echo yes)" "yes"

NEW=$(curl -s -X POST "$BASE/tickets" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"subject":"Diagnose crashed","message":"App closed after I tapped Diagnose."}')
TID=$(str_field "$NEW" '["id"]')
assert_eq "POST /tickets opens a ticket" "$([ -n "$TID" ] && echo yes)" "yes"
assert_eq "POST /tickets starts open with one message" \
  "$(str_field "$NEW" '["status"]')/$(str_field "$NEW" '["message_count"]')" "open/1"

LIST=$(curl -s "$BASE/tickets" -H "Authorization: Bearer $TOKEN")
COUNT=$(printf '%s' "$LIST" | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["items"]))' 2>/dev/null || echo 0)
assert_eq "GET /tickets lists it" "$COUNT" "1"

# --- validation --------------------------------------------------------------
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/tickets" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"subject":"   ","message":"x"}')
assert_eq "blank subject rejected with 422" "$code" "422"

# --- superadmin half ---------------------------------------------------------
if [ -n "$ADMIN_CONTACT" ] && [ -n "$ADMIN_PASSWORD" ]; then
  LOGIN=$(curl -s -X POST "$BASE/auth/login" -H 'Content-Type: application/json' \
    -d "{\"contact\":\"$ADMIN_CONTACT\",\"password\":\"$ADMIN_PASSWORD\"}")
  ATOK=$(str_field "$LOGIN" '["token"]')
  assert_eq "superadmin login returns a token" "$([ -n "$ATOK" ] && echo yes)" "yes"

  ALIST=$(curl -s "$BASE/admin/tickets" -H "Authorization: Bearer $ATOK")
  HAS=$(printf '%s' "$ALIST" | python3 -c \
    "import sys,json;print('yes' if any(t['id']==$TID for t in json.load(sys.stdin)['items']) else 'no')" 2>/dev/null)
  assert_eq "GET /admin/tickets contains our ticket" "$HAS" "yes"
  FNAME=$(printf '%s' "$ALIST" | python3 -c \
    "import sys,json;print([t for t in json.load(sys.stdin)['items'] if t['id']==$TID][0]['farmer']['name'])" 2>/dev/null)
  assert_eq "ticket carries the farmer identity" "$FNAME" "Ticket Farmer"

  REPLY=$(curl -s -X POST "$BASE/admin/tickets/$TID/messages" \
    -H "Authorization: Bearer $ATOK" -H 'Content-Type: application/json' \
    -d '{"body":"Fix is on the way."}')
  assert_eq "admin reply bumps the thread to 2" \
    "$(str_field "$REPLY" '["message_count"]')" "2"

  THREAD=$(curl -s "$BASE/tickets/$TID" -H "Authorization: Bearer $TOKEN")
  LASTFROM=$(printf '%s' "$THREAD" | python3 -c \
    'import sys,json;print(json.load(sys.stdin)["messages"][-1]["from"])' 2>/dev/null)
  assert_eq "farmer sees the reply flagged 'admin'" "$LASTFROM" "admin"

  RES=$(curl -s -X PUT "$BASE/admin/tickets/$TID" -H "Authorization: Bearer $ATOK" \
    -H 'Content-Type: application/json' -d '{"status":"resolved"}')
  assert_eq "PUT /admin/tickets/{id} resolves" "$(str_field "$RES" '["status"]')" "resolved"

  BAD=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "$BASE/admin/tickets/$TID" \
    -H "Authorization: Bearer $ATOK" -H 'Content-Type: application/json' \
    -d '{"status":"bogus"}')
  assert_eq "unknown status rejected with 422" "$BAD" "422"

  REOPEN=$(curl -s -X POST "$BASE/tickets/$TID/messages" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' -d '{"body":"Still broken for me."}')
  assert_eq "farmer reply reopens a resolved ticket" "$(str_field "$REOPEN" '["status"]')" "open"
else
  echo "  (superadmin half skipped — pass admin contact + password to enable)"
fi

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "All smoke checks passed."
else
  echo "$FAILURES check(s) FAILED"
  exit 1
fi
