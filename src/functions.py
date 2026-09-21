import base64
import logging
import secrets
import time
from urllib.parse import quote

import aiohttp

from config import config

logger = logging.getLogger(__name__)


def _decode_vpn_link(link: str) -> str:
    if not link or not link.startswith("vpn://"):
        return ""
    encoded = link[6:]
    try:
        return base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return ""


class PanelAPI:
    """Client for the 3x-ui client API used by the kernel AWG synchronizer."""

    def __init__(self):
        self.session = None
        self.base_url = config.XUI_API_URL.rstrip("/")

    async def _ensure_session(self):
        if self.session is None:
            connector = aiohttp.TCPConnector(ssl=config.XUI_VERIFY_SSL)
            self.session = aiohttp.ClientSession(
                connector=connector,
                headers={
                    "Authorization": f"Bearer {config.XUI_API_TOKEN}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

    async def _request(self, method: str, path: str, payload=None, allow_missing=False):
        await self._ensure_session()
        try:
            async with self.session.request(method, self.base_url + path, json=payload) as resp:
                if allow_missing and resp.status == 404:
                    return None
                if resp.status != 200:
                    logger.error("3x-ui %s %s failed: %s %s", method, path, resp.status, (await resp.text())[:200])
                    return None
                data = await resp.json()
                if allow_missing and isinstance(data, dict) and not data.get("success"):
                    return None
                if not isinstance(data, dict) or not data.get("success"):
                    logger.error("3x-ui %s %s rejected: %s", method, path, data.get("msg") if isinstance(data, dict) else data)
                    return None
                return data.get("obj")
        except Exception:
            logger.exception("3x-ui request error: %s %s", method, path)
            return None

    @staticmethod
    def email_for_telegram(telegram_id: int) -> str:
        return f"tg_{telegram_id}"

    async def get_client(self, email: str):
        return await self._request("GET", "/panel/api/clients/get/" + quote(email, safe=""), allow_missing=True)

    async def create_client(self, telegram_id: int):
        email = self.email_for_telegram(telegram_id)
        if await self.get_client(email):
            return email
        client = {
            "email": email, "subId": secrets.token_hex(8), "totalGB": 0,
            "expiryTime": 0, "limitIp": 0, "limitHwid": 0, "tgId": telegram_id,
            "comment": f"TopVPN Telegram user {telegram_id}", "enable": True,
            "reset": 0, "resetDay": 0, "resetMax": 0, "security": "",
            "trafficReset": "never", "trafficResetDay": 1,
        }
        await self._request("POST", "/panel/api/clients/add", {"client": client, "inboundIds": [config.INBOUND_ID]})
        return email if await self.get_client(email) else None

    async def find_client_id_by_name(self, name: str):
        return name if await self.get_client(name) else None

    async def export_client_vpn_link(self, client_id: str):
        links = await self._request("GET", "/panel/api/clients/links/" + quote(client_id, safe="")) or []
        return next((item for item in links if isinstance(item, str) and item.startswith("vpn://")), None)

    async def export_client_config(self, client_id: str):
        return _decode_vpn_link(await self.export_client_vpn_link(client_id) or "") or None

    async def update_client(self, email: str, **changes) -> bool:
        record = await self.get_client(email)
        if not record:
            return False
        client = record.get("client", record)
        if "id" in client:
            client["id"] = str(client["id"])
        if isinstance(client.get("allowedIPs"), str):
            client["allowedIPs"] = [value.strip() for value in client["allowedIPs"].split(",") if value.strip()]
        client.pop("createdAt", None)
        client.pop("updatedAt", None)
        client.pop("reverse", None)
        client.update(changes)
        await self._request("POST", "/panel/api/clients/update/" + quote(email, safe=""), client)
        updated = await self.get_client(email)
        updated_client = updated.get("client", updated) if updated else {}
        return all(updated_client.get(key) == value for key, value in changes.items())

    async def delete_client(self, client_id: str) -> bool:
        await self._request("POST", "/panel/api/clients/del/" + quote(client_id, safe=""))
        return not bool(await self.get_client(client_id))

    async def traffic(self, email: str):
        return await self._request("GET", "/panel/api/clients/traffic/" + quote(email, safe=""), allow_missing=True)

    async def list_clients(self):
        return await self._request("GET", "/panel/api/clients/list") or []

    async def close(self):
        if self.session:
            await self.session.close()


async def _profile_for(api: PanelAPI, client_id: str):
    vpn_link = await api.export_client_vpn_link(client_id)
    config_text = _decode_vpn_link(vpn_link or "")
    if not vpn_link or not config_text:
        return None
    return {"client_id": client_id, "config": config_text, "vpn_link": vpn_link}


async def create_awg_profile(telegram_id: int):
    api = PanelAPI()
    try:
        client_id = await api.create_client(telegram_id)
        return await _profile_for(api, client_id) if client_id else None
    finally:
        await api.close()


async def delete_client_by_id(client_id: str) -> bool:
    api = PanelAPI()
    try:
        return await api.delete_client(client_id)
    finally:
        await api.close()


async def get_client_stats(client_id: str) -> dict:
    api = PanelAPI()
    try:
        record = await api.get_client(client_id)
        if not record:
            return {"state": "not_found"}
        client = record.get("client", record)
        traffic = await api.traffic(client_id) or {}
        if not client.get("enable", True) or not traffic.get("enable", True):
            state = "disabled"
        else:
            last_online = int(traffic.get("lastOnline") or 0) // 1000
            state = "online" if last_online and time.time() - last_online <= 180 else "offline"
        last_online = int(traffic.get("lastOnline") or 0) // 1000
        return {
            "state": state, "downloadBps": 0, "uploadBps": 0,
            "handshakeAgeSeconds": max(0, int(time.time()) - last_online) if last_online else None,
            "totalDownload": int(traffic.get("down") or 0), "totalUpload": int(traffic.get("up") or 0),
        }
    finally:
        await api.close()


async def get_online_users() -> int:
    api = PanelAPI()
    try:
        now = time.time()
        count = 0
        for client in await api.list_clients():
            email = client.get("email")
            traffic = await api.traffic(email) if email else None
            last_online = int((traffic or {}).get("lastOnline") or 0) // 1000
            if client.get("enable", True) and last_online and now - last_online <= 180:
                count += 1
        return count
    finally:
        await api.close()


async def get_global_stats() -> dict:
    return {"download": 0, "upload": 0}


async def create_static_client(profile_name: str):
    api = PanelAPI()
    try:
        if not await api.get_client(profile_name):
            client = {
                "email": profile_name, "subId": secrets.token_hex(8), "totalGB": 0,
                "expiryTime": 0, "limitIp": 0, "limitHwid": 0, "tgId": 0,
                "comment": "TopVPN static profile", "enable": True, "reset": 0,
                "resetDay": 0, "resetMax": 0, "security": "",
                "trafficReset": "never", "trafficResetDay": 1,
            }
            if await api._request("POST", "/panel/api/clients/add", {"client": client, "inboundIds": [config.INBOUND_ID]}) is None:
                return None
        return await _profile_for(api, profile_name)
    finally:
        await api.close()


async def delete_client_by_name(name: str) -> bool:
    return await delete_client_by_id(name)


async def set_client_enabled(client_id: str, enabled: bool) -> bool:
    api = PanelAPI()
    try:
        return await api.update_client(client_id, enable=enabled)
    finally:
        await api.close()
