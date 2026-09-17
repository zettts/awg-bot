"""Интеграция с RollyPay: создание платежа и опрос статуса.

Примечание: SDK rollypay==0.1.4 не добавляет обязательный заголовок X-Nonce
и не поддерживает поле "test" в create() — поэтому запросы идут через
client.request() напрямую, в обход стандартных обёрток payments.create/get.
"""
import asyncio
import logging
import uuid
from rollypay import RollyPayClient
from rollypay.exceptions import RollyPayError

from config import config

logger = logging.getLogger("rollypay")

_client = None


def _get_client() -> RollyPayClient:
    global _client
    if _client is None:
        _client = RollyPayClient(
            api_key=config.ROLLYPAY_API_KEY,
            base_url="https://api.rollypay.io/api/v1",
        )
    return _client


def _nonce_headers():
    return {"X-Nonce": str(uuid.uuid4())}


async def create_payment(amount_rub: str, order_id: str, description: str, test: bool = False):
    """Создаёт платёж в RollyPay, возвращает (payment_id, pay_url) или (None, None) при ошибке."""
    def _create():
        client = _get_client()
        data = {
            "amount": amount_rub,
            "order_id": order_id,
            "payment_currency": "RUB",
            "payment_method": "sbp",
            "description": description,
            "terminal_id": config.ROLLYPAY_TERMINAL_ID,
            "test": test,
        }
        return client.request("POST", "payments", json=data, headers=_nonce_headers())

    try:
        payment = await asyncio.to_thread(_create)
        return payment["payment_id"], payment["pay_url"]
    except RollyPayError as e:
        logger.error(f"RollyPay create_payment error: {e}")
        return None, None


async def get_payment_status(payment_id: str):
    """Возвращает статус платежа ('created'/'processing'/'paid'/'expired'/'canceled') или None при ошибке."""
    def _get():
        client = _get_client()
        return client.request("GET", f"payments/{payment_id}", headers=_nonce_headers())

    try:
        payment = await asyncio.to_thread(_get)
        return payment["status"]
    except RollyPayError as e:
        logger.error(f"RollyPay get_payment_status error: {e}")
        return None


async def poll_payment_until_final(payment_id: str, poll_interval: int = 8, max_wait: int = 1800):
    """
    Опрашивает статус платежа, пока он не станет финальным (paid/expired/canceled)
    или не истечёт max_wait секунд. Возвращает финальный статус или None по таймауту.
    """
    elapsed = 0
    while elapsed < max_wait:
        status = await get_payment_status(payment_id)
        if status in ("paid", "expired", "canceled"):
            return status
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    logger.warning(f"RollyPay payment {payment_id} polling timed out after {max_wait}s")
    return None
