# WireGuard VPN + Keycloak Authentication + Real-time Revocation

PoC: WireGuard VPN authentication via Keycloak (OIDC) using WireGuard Portal as middleware,
with **real-time VPN revocation** when a user is disabled in Keycloak.

---

## How it works

### The problem

WireGuard is a VPN — it creates an encrypted tunnel between your device and a server.
But WireGuard only understands cryptographic keys. It has no login page, no passwords,
no user management. If someone has the key file (`.conf`), they're in.

Without any management layer:
- Admin manually creates keys, sends `.conf` files to employees
- Employee connects with `wg-quick up client.conf`
- If the employee leaves the company, admin must manually delete their key from the server
- If admin forgets, the ex-employee still has VPN access forever

### Our solution: three layers

**Layer 1 — Keycloak (identity):** Controls who can log in and download a VPN config.
Employees authenticate with username/password/MFA before they can get a `.conf` file.

**Layer 2 — WireGuard Portal (management):** A web UI that sits between Keycloak and
WireGuard. It manages users, generates key pairs, and lets employees download their config.
It also exposes a REST API for automation.

**Layer 3 — WG Access Manager (revocation):** A small service that listens for Keycloak
webhooks. When an admin disables a user in Keycloak, it automatically disables their
VPN peers in WireGuard Portal, which removes them from the server.

### Key concept: `wg0` vs client interfaces

WireGuard creates **network interfaces** (like virtual network cards):

- **`wg0`** is the **server** interface. It runs on the VPN server, listens on port 51820,
  and has a list of allowed peers (clients). Think of it as the "door" — it decides who
  gets in. `sudo wg show wg0` shows all connected peers.

- **`test`** (or `client`) is the **client** interface. It's created on the employee's
  machine when they run `wg-quick up test.conf`. It only knows about one peer: the server.

When a peer is **removed from `wg0`**, the server no longer accepts that client's traffic.
The client can still try to connect (the `.conf` file still exists), but the server
ignores it — like having a key to a lock that's been changed.

---

## Architecture

```
┌──────────┐      1. "Login with         ┌──────────┐
│   User   │ ──────  Keycloak" ────────► │ Keycloak │
│ (browser)│ ◄──── 2. Token (verified) ── │ (port    │
│          │                              │  8080)   │
└────┬─────┘                              └─────┬────┘
     │                                          │
     │ 3. Authenticated → access WG Portal      │ Admin disables user
     ▼                                          ▼
┌──────────────┐                         ┌──────────────────┐
│  WireGuard   │  4. Download .conf      │   WG Access      │
│   Portal     │◄────────────────────────│   Manager        │
│ (port 8888)  │  6. Disable/enable peer │ (webhook receiver│
└──────┬───────┘     via REST API        │  port 5000)      │
       │                                 └──────────────────┘
       │ manages                           5. Webhook fires:
       ▼                                      "user disabled"
┌──────────────┐
│  wg0         │  The server interface. Has a list of allowed peers.
│  (port 51820)│  When a peer is disabled, it's removed from this list.
└──────────────┘
       ║
       ║ encrypted tunnel (only works if peer is in wg0's list)
       ║
┌──────────────┐
│  Client      │  Employee's machine. Runs wg-quick up test.conf
│  (test iface)│  If peer removed from wg0 → tunnel dead, no access.
└──────────────┘
```

### Real-time revocation flow

```
Admin disables user in Keycloak UI
  → keycloak-events extension fires webhook to wg-access-manager
  → wg-access-manager verifies HMAC signature
  → Sees enabled=false in the event
  → Calls WG Portal API: disable all peers for this user
  → WG Portal removes the peer from wg0
  → Server stops accepting that client's traffic
  → Existing .conf file becomes useless

Admin re-enables user → reverse flow → peer added back to wg0 → tunnel works again
```

**How to verify it worked:**
```bash
# BEFORE disabling: peer is listed, has "latest handshake"
sudo wg show wg0    # shows peer with allowed ips 10.10.0.3/32

# AFTER disabling: peer is GONE
sudo wg show wg0    # peer removed from list

# Client side: no handshake = server is ignoring us
sudo wg show test   # no "latest handshake" line = dead tunnel
```

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

### 1. Start services

```bash
docker compose up -d
# Wait ~30 seconds for Keycloak to initialize
```

This starts 4 containers:
- **postgres** — database for Keycloak
- **keycloak** — identity provider (custom build with webhook extension)
- **wg-access-manager** — webhook receiver (FastAPI, auto-disables VPN peers)
- **wg-portal** — WireGuard management UI + REST API

### 2. Configure Keycloak

```bash
./setup-keycloak.sh
```

This creates: realm `demo`, OIDC client `wg-portal`, test user `testuser`,
and enables admin events with the webhook listener.

### 3. Set WG Portal API token

The API token only applies when the admin user is first created. If the admin already
exists, set it manually (one-time):

```bash
docker run --rm -v ./wg-portal-data:/data python:3.12-slim python3 -c "
import sqlite3
conn = sqlite3.connect('/data/sqlite.db')
conn.execute(\"UPDATE users SET api_token='demo-api-token-change-me' WHERE identifier='admin@wgportal.local'\")
conn.commit()
print('API token set')
"
docker restart wg-portal
```

### 4. First login and interface setup

1. Open **http://localhost:8888** → click **"Login with Keycloak"** → `testuser` / `testpass123`
   (this registers the test user in WG Portal)
2. Log in as admin (`admin@wgportal.local` / `admin1234`)
3. Create a WireGuard interface: name=`wg0`, port=`51820`, address=`10.10.0.1/24`

### 5. Create a peer and test VPN

1. As admin in WG Portal, create a peer:
   - Assign to the test user
   - IP Address: `10.10.0.3/32`
   - Allowed IPs: `10.10.0.0/24` (this controls what traffic goes through the tunnel)
2. Download the `.conf` file
3. Connect:
   ```bash
   sudo wg-quick up ./test.conf
   ping 10.10.0.1                    # should get replies
   sudo wg show wg0                  # shows peer with "latest handshake"
   ```

### 6. Test revocation

1. Keep VPN connected
2. Open Keycloak admin: http://localhost:8080 (`admin` / `admin`)
3. Switch to **demo** realm (top-left dropdown)
4. Go to **Users** → click **testuser** → toggle **Enabled** to **OFF** → **Save**
5. Check what happened:
   ```bash
   # Webhook logs — should show "DISABLED" + "Disabled 1 peer(s)"
   docker compose logs wg-access-manager --tail 10

   # Server side — test peer should be GONE from the list
   sudo wg show wg0

   # Client side — no "latest handshake" = server is ignoring us
   sudo wg show test
   ```
6. Re-enable user in Keycloak → peer comes back → tunnel works again

### 7. Disconnect

```bash
sudo wg-quick down ./test.conf
```

If internet breaks: `sudo ip link delete test && sudo resolvconf -d test`

---

## Test results (2026-02-10)

### VPN connection
```
$ sudo wg show wg0
interface: wg0
  public key: RLcRD02/30X1tl5XcrPIEvxgzRH+kakks2Yn6tHZyAY=
  listening port: 51820

peer: bRaRrpXcA45ik6/MhpDXgn0tiDE/6wvBUplnRJRN/ko=    ← test user
  preshared key: (hidden)
  allowed ips: 10.10.0.3/32

$ ping 10.10.0.1
64 bytes from 10.10.0.1: icmp_seq=1 ttl=64 time=0.029 ms
```

### After disabling user in Keycloak
```
$ docker compose logs wg-access-manager --tail 5
wg-access-manager  | Received event: type=admin.USER-UPDATE
wg-access-manager  | User f2404ed1-... DISABLED in Keycloak — disabling VPN peers
wg-access-manager  | Disabled 1 peer(s) for user f2404ed1-...

$ sudo wg show wg0
interface: wg0
  public key: RLcRD02/30X1tl5XcrPIEvxgzRH+kakks2Yn6tHZyAY=
  listening port: 51820

peer: s2Vxmo+aOUv/g3ZGX0zX6HiSJFrUwyQH9igcHHGhtD4=    ← only admin peer remains
  preshared key: (hidden)
  allowed ips: 10.10.0.2/32

# test user's peer is GONE — server removed it
```

### WG Portal UI confirmation
Peer shows: "Peer is disabled, reason: User disabled in Keycloak"

**Full revocation flow verified:**
- Disable user in Keycloak → webhook fires → peer removed from wg0 automatically
- Re-enable user in Keycloak → webhook fires → peer added back to wg0
- Round-trip time: ~2 seconds

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `invalid parameter: redirect_uri` | Set redirect URIs to `http://localhost:8888/*` in Keycloak client settings |
| `lookup keycloak: server misbehaving` | Use `localhost:8080` not `keycloak:8080` in wg-portal config |
| `password too weak` panic | Set admin password to at least 8 characters |
| **Internet stops working** | `sudo wg-quick down ./test.conf` or `sudo ip link delete test && sudo resolvconf -d test` |
| Webhook not firing | Verify `ext-event-webhook` is in realm Event Listeners (setup-keycloak.sh does this) |
| wg-access-manager can't reach WG Portal | Check `extra_hosts` in docker-compose and that WG Portal is running on host |
| API returns 401 | Set API token in SQLite DB (see Setup step 3) |
| keycloak-events NoSuchMethodError | Use v0.35 for Keycloak 26.0 (v0.51 needs KC 26.5+) |

---

## MFA

MFA is a Keycloak setting — no changes needed on the WireGuard side.
In Keycloak admin: Authentication → Flows → browser flow → set OTP Form to **Required**.
Users will be prompted to set up a TOTP app (Google Authenticator, etc.) on next login.

---

## Production notes

- Enable **HTTPS/TLS** on Keycloak and WireGuard Portal
- Change all **passwords and secrets** to strong random values
- Change `WEBHOOK_SECRET` and `WG_PORTAL_API_TOKEN` to long random strings
- To use an **existing Keycloak**: remove postgres + keycloak from docker-compose, update `base_url`

## Credentials (demo only)

| Service | Username | Password | URL |
|---|---|---|---|
| Keycloak admin | `admin` | `admin` | http://localhost:8080 |
| WireGuard Portal admin | `admin@wgportal.local` | `admin1234` | http://localhost:8888 |
| Test user | `testuser` | `testpass123` | via WG Portal (Keycloak login) |
