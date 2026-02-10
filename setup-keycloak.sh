#!/usr/bin/env bash
# Automated Keycloak configuration for the WireGuard PoC.
# Creates realm, OIDC client, test user, and enables admin events.
set -euo pipefail

KC_URL="${KC_URL:-http://localhost:8080}"
ADMIN_USER="${KC_ADMIN_USER:-admin}"
ADMIN_PASS="${KC_ADMIN_PASS:-admin}"

echo "==> Waiting for Keycloak at ${KC_URL}..."
until curl -sf "${KC_URL}/realms/master" > /dev/null 2>&1; do
  sleep 2
done
echo "    Keycloak is up."

echo "==> Getting admin token..."
ADMIN_TOKEN=$(curl -sf -X POST \
  "${KC_URL}/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=${ADMIN_USER}" \
  -d "password=${ADMIN_PASS}" \
  -d "grant_type=password" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

AUTH="Authorization: Bearer ${ADMIN_TOKEN}"

# ── Create realm ──
echo "==> Creating realm 'demo'..."
curl -sf -X POST "${KC_URL}/admin/realms" \
  -H "${AUTH}" -H "Content-Type: application/json" \
  -d '{"realm": "demo", "enabled": true}' \
  && echo "    Created." || echo "    Already exists (or error)."

# ── Create OIDC client for WG Portal ──
echo "==> Creating OIDC client 'wg-portal'..."
curl -sf -X POST "${KC_URL}/admin/realms/demo/clients" \
  -H "${AUTH}" -H "Content-Type: application/json" \
  -d '{
    "clientId": "wg-portal",
    "enabled": true,
    "protocol": "openid-connect",
    "publicClient": false,
    "secret": "wg-portal-secret-123",
    "redirectUris": ["http://localhost:8888/*"],
    "webOrigins": ["http://localhost:8888"],
    "standardFlowEnabled": true
  }' && echo "    Created." || echo "    Already exists (or error)."

# ── Create test user ──
echo "==> Creating test user 'testuser'..."
curl -sf -X POST "${KC_URL}/admin/realms/demo/users" \
  -H "${AUTH}" -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "test@test.com",
    "firstName": "Test",
    "lastName": "User",
    "enabled": true,
    "emailVerified": true,
    "credentials": [{"type": "password", "value": "testpass123", "temporary": false}]
  }' && echo "    Created." || echo "    Already exists (or error)."

# ── Enable admin events with representations ──
echo "==> Enabling admin events with 'Include Representation'..."
curl -sf -X PUT "${KC_URL}/admin/realms/demo" \
  -H "${AUTH}" -H "Content-Type: application/json" \
  -d '{
    "adminEventsEnabled": true,
    "adminEventsDetailsEnabled": true,
    "eventsEnabled": true,
    "eventsListeners": ["jboss-logging", "ext-event-webhook"]
  }' && echo "    Done." || echo "    Failed."

echo ""
echo "=== Keycloak setup complete ==="
echo "  Realm:     demo"
echo "  Client:    wg-portal (secret: wg-portal-secret-123)"
echo "  Test user: testuser / testpass123 (email: test@test.com)"
echo "  Admin events: enabled with representation + webhook listener"
