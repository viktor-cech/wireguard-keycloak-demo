"""WG Portal v2 API client."""

import asyncio
import logging
from base64 import b64encode

import httpx

logger = logging.getLogger(__name__)


class WGPortalClient:
    """Client for WG Portal v2 REST API (/api/v1)."""

    def __init__(self, base_url: str, admin_user: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        # WG Portal uses HTTP Basic Auth: username=admin_user, password=api_token
        credentials = b64encode(f"{admin_user}:{api_token}".encode()).decode()
        self.headers = {"Authorization": f"Basic {credentials}"}

    async def _request(
        self, method: str, path: str, json: dict | None = None, retries: int = 3
    ) -> httpx.Response:
        url = f"{self.base_url}/api/v1{path}"
        backoff = 1
        last_exc = None

        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.request(
                        method, url, headers=self.headers, json=json
                    )
                    resp.raise_for_status()
                    return resp
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                if attempt < retries - 1:
                    logger.warning(
                        "WG Portal request %s %s failed (attempt %d/%d): %s",
                        method, path, attempt + 1, retries, exc,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2

        raise last_exc

    async def list_users(self) -> list[dict]:
        resp = await self._request("GET", "/user/all")
        return resp.json()

    async def find_user_by_email(self, email: str) -> dict | None:
        users = await self.list_users()
        for user in users:
            if user.get("Email", "").lower() == email.lower():
                return user
        return None

    async def list_peers_for_user(self, user_identifier: str) -> list[dict]:
        resp = await self._request("GET", f"/peer/by-user/{user_identifier}")
        return resp.json()

    async def update_peer(self, peer_id: str, data: dict) -> dict:
        resp = await self._request("PUT", f"/peer/by-id/{peer_id}", json=data)
        return resp.json()

    async def disable_peer(self, peer: dict, reason: str = "User disabled in Keycloak") -> dict:
        peer["Disabled"] = True
        peer["DisabledReason"] = reason
        return await self.update_peer(peer["Identifier"], peer)

    async def enable_peer(self, peer: dict) -> dict:
        peer["Disabled"] = False
        peer["DisabledReason"] = ""
        return await self.update_peer(peer["Identifier"], peer)

    async def set_peers_for_user_id(self, user_identifier: str, *, disabled: bool) -> int:
        """Disable or enable all peers for a user by their WG Portal identifier.

        For OIDC users, this is the Keycloak user UUID.
        Returns the number of peers modified.
        """
        peers = await self.list_peers_for_user(user_identifier)
        count = 0

        for peer in peers:
            if disabled and not peer.get("Disabled"):
                await self.disable_peer(peer)
                logger.info("Disabled peer %s (user %s)", peer["Identifier"], user_identifier)
                count += 1
            elif not disabled and peer.get("Disabled"):
                await self.enable_peer(peer)
                logger.info("Enabled peer %s (user %s)", peer["Identifier"], user_identifier)
                count += 1

        return count
