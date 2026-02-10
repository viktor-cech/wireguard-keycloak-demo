"""Webhook receiver for Keycloak admin events → WG Portal peer management."""

import hashlib
import hmac
import json
import logging
import os

from fastapi import FastAPI, Header, HTTPException, Request

from wg_portal import WGPortalClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("wg-access-manager")

app = FastAPI(title="WG Access Manager")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
WG_PORTAL_URL = os.environ.get("WG_PORTAL_URL", "http://host.docker.internal:8888")
WG_PORTAL_ADMIN_USER = os.environ.get("WG_PORTAL_ADMIN_USER", "admin@wgportal.local")
WG_PORTAL_API_TOKEN = os.environ.get("WG_PORTAL_API_TOKEN", "")

wg_client = WGPortalClient(WG_PORTAL_URL, WG_PORTAL_ADMIN_USER, WG_PORTAL_API_TOKEN)


def verify_signature(payload: bytes, signature: str | None) -> bool:
    """Verify HMAC-SHA256 signature from Keycloak webhook."""
    if not WEBHOOK_SECRET:
        logger.warning("WEBHOOK_SECRET not set — skipping signature verification")
        return True
    if not signature:
        return False
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/keycloak")
async def keycloak_webhook(
    request: Request,
    x_keycloak_signature: str | None = Header(None),
):
    body = await request.body()

    if not verify_signature(body, x_keycloak_signature):
        logger.warning("Invalid webhook signature — rejecting request")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("type", "")
    logger.info("Received event: type=%s", event_type)

    # The p2-inc/keycloak-events extension sends admin events with:
    #   type="admin.USER-UPDATE", resourceType="USER", operationType="UPDATE",
    #   resourcePath="users/<uuid>", representation=<JSON string of PUT body>
    resource_type = event.get("resourceType")
    operation_type = event.get("operationType")

    if resource_type != "USER" or operation_type != "UPDATE":
        return {"status": "ignored"}

    # The representation is just the PUT body (e.g. {"enabled": false}),
    # NOT the full user object. Parse it for the enabled flag.
    representation = event.get("representation")
    if isinstance(representation, str):
        try:
            representation = json.loads(representation)
        except json.JSONDecodeError:
            logger.error("Could not parse representation JSON")
            return {"status": "error", "detail": "bad representation"}

    if not representation or "enabled" not in representation:
        logger.info("USER-UPDATE event without 'enabled' field — ignoring")
        return {"status": "ignored"}

    enabled = representation["enabled"]

    # Extract user UUID from resourcePath (format: "users/<uuid>")
    resource_path = event.get("resourcePath", "")
    if not resource_path.startswith("users/"):
        logger.warning("Unexpected resourcePath: %s", resource_path)
        return {"status": "ignored"}
    keycloak_user_id = resource_path.removeprefix("users/")

    # WG Portal uses the Keycloak UUID as the user Identifier for OIDC users.
    # Use it directly to find and toggle peers.
    if not enabled:
        logger.info("User %s DISABLED in Keycloak — disabling VPN peers", keycloak_user_id)
        count = await wg_client.set_peers_for_user_id(keycloak_user_id, disabled=True)
        logger.info("Disabled %d peer(s) for user %s", count, keycloak_user_id)
        return {"status": "peers_disabled", "count": count}
    else:
        logger.info("User %s ENABLED in Keycloak — re-enabling VPN peers", keycloak_user_id)
        count = await wg_client.set_peers_for_user_id(keycloak_user_id, disabled=False)
        logger.info("Enabled %d peer(s) for user %s", count, keycloak_user_id)
        return {"status": "peers_enabled", "count": count}
