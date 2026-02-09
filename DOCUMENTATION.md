# WireGuard VPN + Keycloak Authentication - PoC Documentation

## Table of Contents

1. [Background & Concepts](#1-background--concepts)
2. [Architecture](#2-architecture)
3. [Prerequisites](#3-prerequisites)
4. [Step-by-Step Setup](#4-step-by-step-setup)
5. [Testing the Flow](#5-testing-the-flow)
6. [Troubleshooting](#6-troubleshooting)
7. [Production Considerations](#7-production-considerations)
8. [Research Summary](#8-research-summary)

---

## 1. Background & Concepts

### What is a VPN?

A VPN (Virtual Private Network) creates a secure, encrypted tunnel between an employee's
device and an internal network. It allows remote workers to access internal servers and
services as if they were physically in the office.

```
Without VPN:
  Employee laptop ──── Internet ────✗ Internal servers (blocked)

With VPN:
  Employee laptop ════ encrypted tunnel ════ Internal network ✓
```

### What is WireGuard?

WireGuard is a modern VPN software. It's fast, simple, and built into the Linux kernel.
It uses public/private key pairs for authentication — similar to SSH keys.

**The limitation:** WireGuard only understands cryptographic keys. It has no concept of
users, passwords, login pages, or MFA. If someone has the correct key file, they can
connect — no questions asked.

### What is Keycloak?

Keycloak is an Identity Provider (IdP) — software that manages user identities. It handles:
- Username + password authentication
- MFA (Multi-Factor Authentication) — e.g., TOTP codes from a phone app
- SSO (Single Sign-On) — log in once, access multiple services
- User management — add/remove users, assign roles

Keycloak uses the OIDC (OpenID Connect) protocol — a standard "language" that applications
use to ask Keycloak "is this person who they claim to be?"

### The Problem

WireGuard speaks **keys**. Keycloak speaks **OIDC**. They cannot communicate directly.
WireGuard has no plugin system, no authentication hooks, and no API for external
identity verification.

### The Solution: WireGuard Portal (the middleman)

WireGuard Portal (wg-portal) is an open-source (MIT license) web application that sits
between Keycloak and WireGuard:

1. User clicks "Login with Keycloak" on the portal
2. Portal redirects to Keycloak login page
3. User enters credentials + MFA
4. Keycloak confirms identity back to the portal
5. Portal provisions WireGuard keys and gives the user their VPN config
6. User connects with a standard WireGuard client

---

## 2. Architecture

```
┌──────────────┐
│   Employee   │
│   device     │
└──────┬───────┘
       │ 1. Opens http://localhost:8888
       ▼
┌──────────────┐     2. "Login with        ┌──────────────┐
│  WireGuard   │        Keycloak" click     │              │
│   Portal     │ ─────────────────────────► │   Keycloak   │
│  (port 8888) │ ◄───────────────────────── │  (port 8080) │
│              │     3. User verified ✓     │              │
└──────┬───────┘                            └──────┬───────┘
       │                                           │
       │ 4. Provisions WireGuard                   │ Stores user data in
       │    keys for this user                     │ PostgreSQL database
       ▼                                           ▼
┌──────────────┐                            ┌──────────────┐
│  WireGuard   │                            │  PostgreSQL   │
│   (kernel)   │                            │  (port 5432) │
│ (port 51820) │                            └──────────────┘
└──────────────┘
       ║
  encrypted VPN tunnel
       ║
┌──────────────┐
│   Employee   │
│ WireGuard    │
│   client     │
└──────────────┘
```

| Component | Role | Image | Port |
|---|---|---|---|
| PostgreSQL 16 | Database for Keycloak | `postgres:16` | 5432 (internal) |
| Keycloak 26.0 | Identity Provider | `quay.io/keycloak/keycloak:26.0` | 8080 |
| WireGuard Portal v2 | Web UI + WG management | `wgportal/wg-portal:v2` | 8888 |
| WireGuard | VPN tunnel | Linux kernel module | 51820/UDP |

---

## 3. Prerequisites

```bash
docker --version          # Docker
docker compose version    # Docker Compose
modinfo wireguard         # WireGuard kernel module
wg --version              # WireGuard tools (sudo apt install -y wireguard-tools)
```

---

## 4. Step-by-Step Setup

### Step 1: Create project structure

```bash
mkdir -p wireguard-poc/wg-portal-config wireguard-poc/wg-portal-data
cd wireguard-poc
```

### Step 2: Create `docker-compose.yml`

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

**Key settings explained:**

- `start-dev`: Keycloak runs in dev mode (no HTTPS). Use `start` with TLS for production.
- `NET_ADMIN`: Required for WireGuard Portal to manage network interfaces.
- `network_mode: "host"`: WireGuard Portal runs on the host network so it can manage the
  WireGuard kernel interface. This means it uses `localhost` to reach Keycloak.

### Step 3: Create `wg-portal-config/config.yaml`

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

### Step 4: Start the services

```bash
docker compose up -d
# Wait ~20 seconds for Keycloak to initialize
docker logs wg-portal 2>&1 | tail -5    # Should say "Application startup complete"
```

### Step 5: Configure Keycloak

Keycloak needs to know about WireGuard Portal as an OIDC client.

```bash
# Get admin token
ADMIN_TOKEN=$(curl -s -X POST \
  "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=admin" \
  -d "password=admin" \
  -d "grant_type=password" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Create "demo" realm
curl -s -X POST "http://localhost:8080/admin/realms" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"realm": "demo", "enabled": true, "displayName": "Demo Realm"}'

# Create OIDC client for WireGuard Portal
curl -s -X POST "http://localhost:8080/admin/realms/demo/clients" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "wg-portal",
    "name": "WireGuard Portal",
    "enabled": true,
    "protocol": "openid-connect",
    "publicClient": false,
    "secret": "wg-portal-secret-123",
    "redirectUris": ["http://localhost:8888/*"],
    "webOrigins": ["http://localhost:8888"],
    "standardFlowEnabled": true,
    "directAccessGrantsEnabled": false
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

### Step 6: Restart WireGuard Portal

```bash
docker restart wg-portal
```

---

## 5. Testing the Flow

### Keycloak login test

1. Open **http://localhost:8888**
2. Click **"Login with Keycloak"**
3. Enter `testuser` / `testpass123`
4. You are redirected back to WireGuard Portal as an authenticated user

**Note:** If you're already logged into Keycloak, it will skip the login form (SSO).
Use an incognito window to test the full flow.

### Creating a WireGuard interface (server-side)

Before clients can connect, an admin must create a WireGuard interface in the portal:

1. Log into WireGuard Portal as admin: `admin@wgportal.local` / `admin1234`
2. Create a new interface with:
   - **Identifier**: `wg0`
   - **Display Name**: `Demo VPN`
   - **Listen Port**: `51820`
   - **Addresses**: `10.10.0.1/24`
   - **Mode**: `server`
3. The portal will auto-generate server keys and bring up the `wg0` interface

### Creating a VPN peer (client config)

1. In the `wg0` interface, click **"+"** to create a new peer
2. Fill in **Display Name** (e.g. `Test Laptop`), leave keys empty (auto-generated)
3. Save, then edit the peer (gear icon) to set:
   - **IP Addresses**: `10.10.0.2/32`
   - **Allowed IPs** (server side): `10.10.0.2/32`
4. Click **"Download Configuration"**
5. **Fix the downloaded config** — change `AllowedIPs` in `[Peer]` section:

```diff
- AllowedIPs = 10.10.0.2/32
+ AllowedIPs = 10.10.0.0/24
```

### Connecting with the WireGuard client

```bash
# Start the VPN tunnel
sudo wg-quick up /path/to/client.conf

# Verify the tunnel is up
sudo wg show

# Test connectivity — ping the VPN server
ping 10.10.0.1

# IMPORTANT: Disconnect when done!
sudo wg-quick down /path/to/client.conf
```

### Verified test results (2026-02-09)

The following was tested and confirmed working:

```
$ sudo wg show
interface: wg0
  public key: RLcRD02/30X1tl5XcrPIEvxgzRH+kakks2Yn6tHZyAY=
  listening port: 51820

peer: s2Vxmo+aOUv/g3ZGX0zX6HiSJFrUwyQH9igcHHGhtD4=
  preshared key: (hidden)
  endpoint: 127.0.0.1:41510
  allowed ips: 10.10.0.2/32
  latest handshake: 6 seconds ago
  transfer: 180 B received, 92 B sent

$ ping 10.10.0.1
PING 10.10.0.1 (10.10.0.1) 56(84) bytes of data.
64 bytes from 10.10.0.1: icmp_seq=1 ttl=64 time=0.044 ms
64 bytes from 10.10.0.1: icmp_seq=2 ttl=64 time=0.031 ms
--- 5 packets transmitted, 5 received, 0% packet loss ---
```

**Full flow verified:**
- Keycloak authentication (OIDC) → WireGuard Portal login
- WireGuard Portal → peer provisioning with keys
- WireGuard client → server handshake and encrypted tunnel
- Ping through VPN tunnel → 0% packet loss

---

## 6. Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `invalid parameter: redirect_uri` | Redirect URI mismatch in Keycloak | Set Valid Redirect URIs to `http://localhost:8888/*` in Keycloak client settings |
| `lookup keycloak: server misbehaving` | wg-portal uses host network, can't resolve Docker hostnames | Use `http://localhost:8080/realms/demo` as `base_url` (not `http://keycloak:8080`) |
| `password too weak` panic | Admin password shorter than 8 chars | Set `admin_password` to at least 8 characters |
| **Internet stops working** | VPN client changes DNS settings | Run `sudo wg-quick down ./client.conf` or `sudo ip link delete client && sudo resolvconf -d client` |

Check logs:
```bash
docker logs wg-portal 2>&1 | tail -20          # WireGuard Portal logs
docker logs wireguard-poc-keycloak-1 2>&1 | tail -20   # Keycloak logs
```

---

## 7. Production Considerations

- Enable **HTTPS/TLS** on Keycloak (`start` instead of `start-dev`) and WireGuard Portal
- Change all **passwords and secrets** to strong random values
- Configure **MFA** in Keycloak: Authentication → Flows → browser flow → set OTP Form
  to **Required**. Users will be prompted to set up a TOTP app (Google Authenticator,
  FreeOTP, etc.) on their next login. No changes needed on the WireGuard Portal side.
- Enable **event logging** in Keycloak for audit trail
- To use an **existing Keycloak**: remove postgres + keycloak from docker-compose, update `base_url` to point to your instance

---

## 8. Research Summary

### Key finding

**WireGuard cannot integrate with Keycloak directly.** A middleware layer is always needed.

### Solutions evaluated

| Solution | License | Keycloak OIDC | Verdict |
|---|---|---|---|
| **WireGuard Portal** | MIT (free, no limits) | Built-in | **Selected** |
| Defguard | Apache 2.0 (OIDC is enterprise/paid only) | Enterprise only | Too expensive |
| Firezone | Apache 2.0 (v1 deprecated, v2 cloud-only) | v1 only | Not viable |
| Headscale | BSD-3 | Built-in | Uses Tailscale protocol, not standard WireGuard |
| NetBird | BSD-3 | Built-in | More complex, requires NetBird clients |

### Useful links

- [WireGuard Portal docs](https://wgportal.org) | [GitHub](https://github.com/h44z/wg-portal)
- [Keycloak docs](https://www.keycloak.org/documentation)
- [WireGuard](https://www.wireguard.com)

---

## Quick Start (TL;DR)

```bash
# 1. Clone the repo
git clone <repo-url> && cd wireguard-poc

# 2. Start all services
docker compose up -d

# 3. Wait 20 seconds for Keycloak to start, then configure it
#    (create realm, OIDC client, test user — see Step 5 for API commands)

# 4. Restart WireGuard Portal so it connects to Keycloak
docker restart wg-portal

# 5. Open http://localhost:8888 → "Login with Keycloak" → testuser / testpass123

# 6. As admin (admin@wgportal.local / admin1234), create a WireGuard interface and peer

# 7. Download the client config, fix AllowedIPs if needed, then connect:
sudo wg-quick up ./client.conf

# 8. Test the VPN tunnel
ping 10.10.0.1

# 9. IMPORTANT: Disconnect when done (otherwise DNS breaks and you lose internet!)
sudo wg-quick down ./client.conf

# Stop everything
docker compose down

# Stop and delete all data (including Keycloak database)
docker compose down -v
```

## Credentials (demo only)

| Service | Username | Password | URL |
|---|---|---|---|
| Keycloak admin | `admin` | `admin` | http://localhost:8080 |
| WireGuard Portal admin | `admin@wgportal.local` | `admin1234` | http://localhost:8888 |
| Test user | `testuser` | `testpass123` | Login via WG Portal |

## WARNINGS

- **Always disconnect the VPN client when done testing!** Otherwise you lose internet.
- If you lose internet: `sudo ip link delete client && sudo resolvconf -d client`
- All credentials are demo-only. Change them before any production use.
