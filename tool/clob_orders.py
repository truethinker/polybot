from __future__ import annotations

from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, CreateOrderOptions

from tool.config import Config


def _parse_clob_token_ids(market: dict) -> list[str]:
    v = market.get("clobTokenIds")
    if v is None:
        raise RuntimeError("Market no trae clobTokenIds")

    if isinstance(v, list):
        return [str(x) for x in v]

    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            import json
            arr = json.loads(s)
            if not isinstance(arr, list) or len(arr) < 2:
                raise RuntimeError("clobTokenIds no tiene 2 elementos")
            return [str(x) for x in arr]

    raise RuntimeError(f"Formato clobTokenIds inesperado: {type(v)}")


def _mk_client(cfg: Config) -> ClobClient:
    api_creds = ApiCreds(
        api_key=cfg.clob_api_key,
        api_secret=cfg.clob_api_secret,
        api_passphrase=cfg.clob_api_passphrase,
    )

    return ClobClient(
        host=cfg.clob_host.rstrip("/"),
        chain_id=cfg.chain_id,
        key=cfg.private_key,                 # SDK actual: 'key'
        creds=api_creds,
        signature_type=cfg.signature_type,   # 0 o 1 según tu setup
        funder=cfg.funder_address,           # address que paga colateral
    )


def _create_signed_order(client: ClobClient, order: OrderArgs, opts: CreateOrderOptions | None):
    """
    Compatibilidad entre versiones del SDK:
    - Algunas: create_order(order)
    - Otras:  create_order(order, opts)
    - Otras:  create_order(order, options=opts)
    """
    if opts is None:
        return client.create_order(order)

    # 1) create_order(order, opts)
    try:
        return client.create_order(order, opts)
    except TypeError:
        pass

    # 2) create_order(order, options=opts)
    try:
        return client.create_order(order, options=opts)
    except TypeError:
        pass

    # 3) fallback: create_order(order)
    return client.create_order(order)


def place_dual_orders_for_market(cfg: Config, market: dict) -> dict[str, Any]:
    slug = market.get("slug", "?")

    if market.get("closed") is True:
        raise RuntimeError(f"Market cerrado: {slug}")
    if market.get("acceptingOrders") is False:
        raise RuntimeError(f"Market no acepta órdenes: {slug}")

    token_up, token_down = _parse_clob_token_ids(market)[0:2]

    tick_size = str(market.get("orderPriceMinTickSize", "0.01"))
    neg_risk = bool(market.get("negRisk", False))
    maker_fee_bps = int(market.get("makerBaseFee", 0))  # en tu caso: 1000

    if cfg.dry_run:
        return {
            "slug": slug,
            "dry_run": True,
            "meta": {"tick_size": tick_size, "neg_risk": neg_risk, "maker_fee_bps": maker_fee_bps},
            "up": {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up},
            "down": {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down},
        }

    client = _mk_client(cfg)

    # Opciones (si tu SDK las soporta)
    opts = CreateOrderOptions(
        tick_size=tick_size,
        neg_risk=neg_risk,
    )

    up_order = OrderArgs(
        token_id=token_up,
        price=cfg.price_up,
        size=cfg.size_up,
        side="BUY",
        fee_rate_bps=maker_fee_bps,
    )
    down_order = OrderArgs(
        token_id=token_down,
        price=cfg.price_down,
        size=cfg.size_down,
        side="BUY",
        fee_rate_bps=maker_fee_bps,
    )

    signed_up = _create_signed_order(client, up_order, opts)
    signed_down = _create_signed_order(client, down_order, opts)

    up_resp = client.post_order(signed_up)
    down_resp = client.post_order(signed_down)

    return {
        "slug": slug,
        "meta": {"tick_size": tick_size, "neg_risk": neg_risk, "maker_fee_bps": maker_fee_bps},
        "up": up_resp,
        "down": down_resp,
    }
