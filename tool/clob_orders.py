from __future__ import annotations
from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions

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


def _tick_size(market: dict) -> str:
    return str(market.get("orderPriceMinTickSize") or market.get("tick_size") or "0.01")


def _maker_fee_bps(market: dict) -> int:
    v = market.get("makerBaseFee") or market.get("maker_base_fee") or 0
    try:
        return int(v)
    except Exception:
        return 0


def _mk_client(cfg: Config) -> ClobClient:
    api_creds = ApiCreds(
        api_key=cfg.clob_api_key,
        api_secret=cfg.clob_api_secret,
        api_passphrase=cfg.clob_api_passphrase,
    )

    # Nota: el SDK actual usa key=..., creds=..., signature_type=..., funder=...
    # USE_DERIVED_CREDS depende de versión; lo pasamos solo si existe el param
    kwargs = dict(
        host=cfg.clob_host.rstrip("/"),
        chain_id=cfg.chain_id,
        key=cfg.private_key,
        creds=api_creds,
        signature_type=cfg.signature_type,
        funder=cfg.funder_address,
    )

    try:
        return ClobClient(**kwargs, use_derived_creds=cfg.use_derived_creds)  # type: ignore
    except TypeError:
        # si tu versión no acepta use_derived_creds
        return ClobClient(**kwargs)


def place_dual_orders_for_market(cfg: Config, market: dict) -> dict[str, Any]:
    slug = market.get("slug", "?")

    if market.get("closed") is True:
        raise RuntimeError(f"Market cerrado: {slug}")
    if market.get("acceptingOrders") is False:
        raise RuntimeError(f"Market no acepta órdenes: {slug}")

    token_up, token_down = _parse_clob_token_ids(market)[0:2]

    tick_size = _tick_size(market)
    neg_risk = bool(market.get("negRisk", False))
    maker_fee_bps = _maker_fee_bps(market)

    if cfg.dry_run:
        return {
            "slug": slug,
            "dry_run": True,
            "meta": {"tick_size": tick_size, "neg_risk": neg_risk, "maker_fee_bps": maker_fee_bps},
            "up": {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up},
            "down": {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down},
        }

    client = _mk_client(cfg)

    # opciones de orden (tick + negRisk)
    opts = CreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

    # fee_rate_bps: debe coincidir con el maker fee del market (tu debug: 1000)
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

    signed_up = client.create_order(up_order, OrderType.GTC, opts)
    signed_down = client.create_order(down_order, OrderType.GTC, opts)

    up_resp = client.post_order(signed_up)
    down_resp = client.post_order(signed_down)

    return {
        "slug": slug,
        "meta": {"tick_size": tick_size, "neg_risk": neg_risk, "maker_fee_bps": maker_fee_bps},
        "up": up_resp,
        "down": down_resp,
    }
