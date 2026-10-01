#!/usr/bin/env bash
# End-to-end smoke test for the farmer auth + history endpoints.
# Usage: scripts/auth_smoke.sh [base-url]   (default http://127.0.0.1:8000)
set -uo pipefail

BASE="${1:-http://127.0.0.1:8000}"
CONTACT="smoke-$(date +%s)-${RANDOM}@example.com"
PASS="potato1234"
FAILURES=0

pass()  { printf '  %-44s OK\n'   "$1"; }
fail()  { printf '  %-44s FAIL\n' "$1"; FAILURES=$((FAILURES + 1)); }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (got: ${2:-<empty>})"; fi; }

# str_field '<json>' '["token"]'  -> extracted value, or empty on any error
str_field() {
  printf '%s' "$1" | python3 -c 'import sys,json;print(json.load(sys.stdin)'"$2"')' 2>/dev/null || true
}

echo "Smoke-testing $BASE"

# --- register ---------------------------------------------------------------
REG=$(curl -s -X POST "$BASE/auth/register" -H 'Content-Type: application/json' \
  -d "{\"contact\":\"$CONTACT\",\"name\":\"Smoke Tester\",\"password\":\"$PASS\"}")
TOKEN=$(str_field "$REG" '["token"]')

assert_eq "POST /auth/register returns a token" "$([ -n "$TOKEN" ] && echo yes)" "yes"
assert_eq "POST /auth/register returns display name" \
  "$(str_field "$REG" '["user"]["name"]')" "Smoke Tester"

# --- session ----------------------------------------------------------------
ME=$(curl -s "$BASE/auth/me" -H "Authorization: Bearer $TOKEN")
assert_eq "GET /auth/me sees the farmer" "$(str_field "$ME" '["name"]')" "Smoke Tester"

# --- history ----------------------------------------------------------------
ITEM_ID="$(date +%s)000"
PUT=$(curl -s -X PUT "$BASE/history" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"items\":[{\"id\":\"$ITEM_ID\",\"class\":\"Healthy\",\"confidence\":0.98,\"model\":\"Ensemble\",\"timestamp\":\"now\",\"imageUri\":\"file:///tmp/x.jpg\"}]}")
assert_eq "PUT /history upserts one item" "$(str_field "$PUT" '["upserted"]')" "1"

HIST=$(curl -s "$BASE/history" -H "Authorization: Bearer $TOKEN")
COUNT=$(printf '%s' "$HIST" | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["items"]))' 2>/dev/null || echo 0)
assert_eq "GET /history returns it" "$COUNT" "1"

if printf '%s' "$HIST" | grep -q imageUri; then
  fail "imageUri was stripped on upload"
else
  pass "imageUri was stripped on upload"
fi

# --- existing contract untouched -------------------------------------------
assert_eq "GET /ping unchanged" "$(curl -s "$BASE/ping")" "Hello, I am alive"
if curl -s "$BASE/models" | grep -q '"models"'; then
  pass "GET /models unchanged"
else
  fail "GET /models unchanged"
fi

# --- logout -----------------------------------------------------------------
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/auth/logout" -H "Authorization: Bearer $TOKEN")
assert_eq "POST /auth/logout returns 204" "$code" "204"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/auth/me" -H "Authorization: Bearer $TOKEN")
assert_eq "revoked token rejected with 401" "$code" "401"

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "All smoke checks passed."
else
  echo "$FAILURES check(s) FAILED"
  exit 1
fi
