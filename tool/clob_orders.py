from __future__ import annotations

from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
from py_clob_client.order_builder.constants import BUY  # <-- NO Side enum

from tool.config import Config


def _parse_clob_token_ids(market: dict) -> list[str]:
    v = market.get("clobTokenIds")
    if v is None:
        raise RuntimeError("Market no trae clobTokenIds")

    if isinstance(v, list):
        return [str(x) for x in v]

    if isinstance(v, str):
        import json
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            arr = json.loads(s)
            if not isinstance(arr, list) or len(arr) < 2:
                raise RuntimeError("clobTokenIds no tiene 2 elementos")
            return [str(x) for x in arr]

    raise RuntimeError(f"Formato clobTokenIds inesperado: {type(v)}")


def _mk_client(cfg: Config) -> ClobClient:
    # Inicialización según el README oficial del SDK:
    # - key=PRIVATE_KEY
    # - signature_type: 0 EOA/Metamask, 1 Magic/email, 2 proxy
    # - funder: address que realmente tiene fondos
    client = ClobClient(
        cfg.clob_host.rstrip("/"),
        key=cfg.private_key,
        chain_id=cfg.chain_id,
        signature_type=cfg.signature_type,
        funder=cfg.funder_address,
    )

    # Opción A (recomendada): derivar creds automáticamente (menos lío)
    # client.set_api_creds(client.create_or_derive_api_creds())

    # Opción B: usar tus env vars de API creds
    api_creds = ApiCreds(
        api_key=cfg.clob_api_key,
        api_secret=cfg.clob_api_secret,
        api_passphrase=cfg.clob_api_passphrase,
    )
    client.set_api_creds(api_creds)

    return client


def place_dual_orders_for_market(cfg: Config, market: dict) -> dict[str, Any]:
    slug = market.get("slug", "?")
    if market.get("closed") is True:
        raise RuntimeError(f"Market cerrado: {slug}")
    if market.get("acceptingOrders") is False:
        raise RuntimeError(f"Market no acepta órdenes: {slug}")

    token_up, token_down = _parse_clob_token_ids(market)[:2]

    if cfg.dry_run:
        return {
            "slug": slug,
            "dry_run": True,
            "up": {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up},
            "down": {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down},
        }

    client = _mk_client(cfg)

    up_order = OrderArgs(token_id=token_up, price=cfg.price_up, size=cfg.size_up, side=BUY)
    down_order = OrderArgs(token_id=token_down, price=cfg.price_down, size=cfg.size_down, side=BUY)

    signed_up = client.create_order(up_order)
    signed_down = client.create_order(down_order)

    up_resp = client.post_order(signed_up, OrderType.GTC)
    down_resp = client.post_order(signed_down, OrderType.GTC)

    return {"slug": slug, "up": up_resp, "down": down_resp}

def _outcomes(market: dict) -> list[str]:
    v = market.get("outcomes")
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        import json
        try:
            arr = json.loads(v)
            if isinstance(arr, list):
                return [str(x) for x in arr]
        except Exception:
            pass
    return ["Up", "Down"]
    
def _tick_size(market: dict) -> str:
    # Gamma suele traer el tick mínimo aquí
    v = market.get("orderPriceMinTickSize") or market.get("tick_size") or "0.01"
    return str(v)

def _maker_fee_bps(market: dict) -> int:
    # En tu JSON venía makerBaseFee=1000
    v = market.get("makerBaseFee") or market.get("maker_base_fee") or 0
    try:
        return int(v)
    except Exception:
        return 0


