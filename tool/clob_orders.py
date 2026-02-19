from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, Side
from py_clob_client.constants import POLYGON

from tool.config import Config

def _parse_clob_token_ids(market: dict) -> list[str]:
    """
    Gamma devuelve clobTokenIds como string JSON tipo:
    "[\"tokenUp\",\"tokenDown\"]"
    o como lista ya parseada.
    """
    v = market.get("clobTokenIds")
    if v is None:
        raise RuntimeError("Market no trae clobTokenIds")

    if isinstance(v, list):
        return [str(x) for x in v]

    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            # parse minimal sin json lib extra
            import json
            arr = json.loads(s)
            if not isinstance(arr, list) or len(arr) < 2:
                raise RuntimeError("clobTokenIds no tiene 2 elementos")
            return [str(x) for x in arr]

    raise RuntimeError(f"Formato clobTokenIds inesperado: {type(v)}")

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

def _mk_client(cfg: Config) -> ClobClient:
    host = cfg.clob_host.rstrip("/")

    client = ClobClient(
        host=host,
        chain_id=POLYGON,
        private_key=cfg.private_key,
    )

    # Deriva (o crea) credenciales API para endpoints autenticados
    client.create_or_derive_api_key()

    return client

def place_dual_orders_for_market(cfg: Config, market: dict) -> dict[str, Any]:
    """
    Pone 2 limit orders (Up y Down) para un market.
    IMPORTANTE: aquí asumimos mapping outcomes[0]=Up, outcomes[1]=Down
    que es lo habitual en estos 5m (y coincide con tu JSON). Ajustable.
    """
    slug = market.get("slug", "?")
    if market.get("closed") is True:
        raise RuntimeError(f"Market cerrado: {slug}")
    if market.get("acceptingOrders") is False:
        raise RuntimeError(f"Market no acepta órdenes: {slug}")

    token_ids = _parse_clob_token_ids(market)
    outs = _outcomes(market)

    # En tu JSON: outcomes ["Up","Down"] y clobTokenIds [UpToken, DownToken]
    # Si algún día viniese invertido, aquí lo verás en logs y lo ajustamos.
    token_up = token_ids[0]
    token_down = token_ids[1]

    if cfg.dry_run:
        return {
            "slug": slug,
            "dry_run": True,
            "up": {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up},
            "down": {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down},
        }

    client = _mk_client(cfg)

    # BUY = quieres comprar shares a ese precio.
    # (Si quisieras “poner liquidez” de venta, sería Side.SELL.)
    up_order = OrderArgs(
        price=cfg.price_up,
        size=cfg.size_up,
        side=Side.BUY,
        token_id=token_up,
    )

    down_order = OrderArgs(
        price=cfg.price_down,
        size=cfg.size_down,
        side=Side.BUY,
        token_id=token_down,
    )

    # create + post
    signed_up = client.create_order(up_order, order_type=OrderType.GTC)
    signed_down = client.create_order(down_order, order_type=OrderType.GTC)

    up_resp = client.post_order(signed_up)
    down_resp = client.post_order(signed_down)

    return {
        "slug": slug,
        "up": {"token_id": token_up, "resp": up_resp},
        "down": {"token_id": token_down, "resp": down_resp},
        "outcomes": outs,
    }
