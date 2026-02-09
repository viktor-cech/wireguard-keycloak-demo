# WireGuard VPN + Keycloak Authentication

PoC: WireGuard VPN authentication via Keycloak (OIDC) using WireGuard Portal as middleware.

---

## How it works

WireGuard is a VPN — it creates an encrypted tunnel between your device and a server.
But WireGuard only understands cryptographic keys. It has no login page, no passwords,
no MFA. If someone has the key file, they're in.

**Without Keycloak (pure WireGuard):**
```
Admin manually creates a key pair
Admin manually puts it in a .conf file
Admin sends the .conf file to the employee via email/Slack/whatever
Employee runs: wg-quick up client.conf
Employee pings 10.10.0.1 ✓

WORKS FINE. No Keycloak needed.
```

**With Keycloak (what we built):**
```
Employee opens WireGuard Portal in browser
Portal says "log in via Keycloak first"
Employee logs in with username + password + MFA
Portal generates the key pair FOR the employee
Employee downloads the .conf file from the portal
Employee runs: wg-quick up client.conf
Employee pings 10.10.0.1 ✓

THE PING IS IDENTICAL. WireGuard doesn't know Keycloak exists.
```

**So what's the point of Keycloak?** It controls **who gets the .conf file**:

- Without Keycloak: keys are lying on a table, anyone can grab them
- With Keycloak: keys are in a locked safe, you need to prove your identity to get them
- The key itself works the same either way

Keycloak adds: login with credentials, MFA, logging of who connected when,
easy user management (disable user → no more VPN access).

---

## Architecture

```
┌──────────┐      1. "Login with         ┌──────────┐
│   User   │ ──────  Keycloak" ────────► │ Keycloak │
│ (browser)│ ◄──── 2. Token (verified) ── │ (port    │
│          │                              │  8080)   │
└────┬─────┘                              └──────────┘
     │
     │ 3. Authenticated → can access WireGuard Portal
     ▼
┌──────────────┐
│  WireGuard   │  4. User downloads .conf file (keys + server IP)
│   Portal     │
│ (port 8888)  │
└──────────────┘

      Then, separately:

┌──────────┐                          ┌──────────────┐
│   User   │ ═══ encrypted tunnel ═══ │  WireGuard   │
│ (wg-quick│     using the .conf      │   server     │
│  client) │     file from portal     │ (port 51820) │
└──────────┘                          └──────────────┘
                                             │
                                       Internal network
                                       (databases, servers, etc.)
```

**Steps 1-4** happen in the browser. After that, the user connects with a standard
WireGuard client using the downloaded config. WireGuard itself never talks to Keycloak.

---

## Prerequisites

```bash
docker --version          # Docker
docker compose version    # Docker Compose
modinfo wireguard         # WireGuard kernel module
wg --version              # WireGuard tools (sudo apt install -y wireguard-tools)
```

---

## Setup

### 1. Create project structure

```bash
mkdir -p wireguard-poc/wg-portal-config wireguard-poc/wg-portal-data
cd wireguard-poc
```

### 2. Create `docker-compose.yml`

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: keycloak
      POSTGRES_USER: keycloak
      POSTGRES_PASSWORD: keycloak_db_pass
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U keycloak"]
      interval: 5s
      timeout: 5s
      retries: 5

  keycloak:
    image: quay.io/keycloak/keycloak:26.0
    command: start-dev
    environment:
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://postgres:5432/keycloak
      KC_DB_USERNAME: keycloak
      KC_DB_PASSWORD: keycloak_db_pass
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin
    ports:
      - "8080:8080"
    depends_on:
      postgres:
        condition: service_healthy

  wg-portal:
    image: wgportal/wg-portal:v2
    container_name: wg-portal
    restart: unless-stopped
    cap_add:
      - NET_ADMIN
    network_mode: "host"
    volumes:
      - /etc/wireguard:/etc/wireguard
      - ./wg-portal-data:/app/data
      - ./wg-portal-config:/app/config
    depends_on:
      - keycloak

volumes:
  postgres_data:
```

### 3. Create `wg-portal-config/config.yaml`

```yaml
core:
  admin_user: admin@wgportal.local
  admin_password: admin1234
  create_default_peer: false
  self_provisioning_allowed: true
  import_existing: true
  restore_state: true

web:
  listening_address: :8888
  external_url: http://localhost:8888
  site_title: WireGuard Portal Demo
  session_secret: demo-session-secret-change-me
  csrf_secret: demo-csrf-secret-change-me
  request_logging: true

auth:
  min_password_length: 8
  oidc:
    - provider_name: keycloak
      display_name: "Login with Keycloak"
      base_url: "http://localhost:8080/realms/demo"
      client_id: "wg-portal"
      client_secret: "wg-portal-secret-123"
      extra_scopes:
        - profile
        - email
      registration_enabled: true
      log_user_info: true

advanced:
  log_level: debug
  log_pretty: true
  start_listen_port: 51820
  start_cidr_v4: 10.10.0.0/24
  use_ip_v6: false
  config_storage_path: /etc/wireguard
```

### 4. Start services

```bash
docker compose up -d
# Wait ~20 seconds for Keycloak to initialize
```

### 5. Configure Keycloak

```bash
# Get admin token
ADMIN_TOKEN=$(curl -s -X POST \
  "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=admin" \
  -d "password=admin" \
  -d "grant_type=password" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Create realm
curl -s -X POST "http://localhost:8080/admin/realms" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"realm": "demo", "enabled": true}'

# Create OIDC client for WireGuard Portal
curl -s -X POST "http://localhost:8080/admin/realms/demo/clients" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "wg-portal",
    "enabled": true,
    "protocol": "openid-connect",
    "publicClient": false,
    "secret": "wg-portal-secret-123",
    "redirectUris": ["http://localhost:8888/*"],
    "webOrigins": ["http://localhost:8888"],
    "standardFlowEnabled": true
  }'

# Create test user
curl -s -X POST "http://localhost:8080/admin/realms/demo/users" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "test@test.com",
    "firstName": "Test",
    "lastName": "User",
    "enabled": true,
    "emailVerified": true,
    "credentials": [{"type": "password", "value": "testpass123", "temporary": false}]
  }'
```

### 6. Restart WireGuard Portal and test

```bash
docker restart wg-portal
```

1. Open **http://localhost:8888** → click **"Login with Keycloak"** → `testuser` / `testpass123`
2. Log in as admin (`admin@wgportal.local` / `admin1234`) and create a WireGuard interface (`wg0`, port `51820`, address `10.10.0.1/24`)
3. Create a peer, set IP to `10.10.0.2/32`, download the `.conf` file
4. Fix `AllowedIPs` in the `[Peer]` section of the downloaded config:
   ```diff
   - AllowedIPs = 10.10.0.2/32
   + AllowedIPs = 10.10.0.0/24
   ```
5. Connect and test:
   ```bash
   sudo wg-quick up ./client.conf
   ping 10.10.0.1                      # should get replies
   sudo wg-quick down ./client.conf    # ALWAYS disconnect when done!
   ```

---

## Test results (2026-02-09)

```
$ sudo wg show
interface: wg0
  public key: RLcRD02/30X1tl5XcrPIEvxgzRH+kakks2Yn6tHZyAY=
  listening port: 51820

peer: s2Vxmo+aOUv/g3ZGX0zX6HiSJFrUwyQH9igcHHGhtD4=
  endpoint: 127.0.0.1:41510
  latest handshake: 6 seconds ago
  transfer: 180 B received, 92 B sent

$ ping 10.10.0.1
64 bytes from 10.10.0.1: icmp_seq=1 ttl=64 time=0.044 ms
--- 5 packets transmitted, 5 received, 0% packet loss ---
```

**Full flow verified:**
- Keycloak login (OIDC) → WireGuard Portal access
- Download .conf from portal → connect with WireGuard client
- Encrypted VPN tunnel works, 0% packet loss

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `invalid parameter: redirect_uri` | Set redirect URIs to `http://localhost:8888/*` in Keycloak client settings |
| `lookup keycloak: server misbehaving` | Use `localhost:8080` not `keycloak:8080` in wg-portal config |
| `password too weak` panic | Set admin password to at least 8 characters |
| **Internet stops working** | `sudo wg-quick down ./client.conf` or `sudo ip link delete client && sudo resolvconf -d client` |

---

## MFA

MFA is a Keycloak setting — no changes needed on the WireGuard side.
In Keycloak admin: Authentication → Flows → browser flow → set OTP Form to **Required**.
Users will be prompted to set up a TOTP app (Google Authenticator, etc.) on next login.

---

## Production notes

- Enable **HTTPS/TLS** on Keycloak and WireGuard Portal
- Change all **passwords and secrets** to strong random values
- To use an **existing Keycloak**: remove postgres + keycloak from docker-compose, update `base_url`

## Credentials (demo only)

| Service | Username | Password | URL |
|---|---|---|---|
| Keycloak admin | `admin` | `admin` | http://localhost:8080 |
| WireGuard Portal | `admin@wgportal.local` | `admin1234` | http://localhost:8888 |
| Test user | `testuser` | `testpass123` | via WG Portal |
