from __future__ import annotations

from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from tool.config import Config


def _parse_clob_token_ids(market: dict) -> list[str]:
    v = market.get("clobTokenIds")
    if v is None:
        raise RuntimeError("Market no trae clobTokenIds")

    if isinstance(v, list):
        if len(v) < 2:
            raise RuntimeError("clobTokenIds no tiene 2 elementos")
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
    client = ClobClient(
        cfg.clob_host.rstrip("/"),
        key=cfg.private_key,
        chain_id=cfg.chain_id,
        signature_type=cfg.signature_type,
        funder=cfg.funder_address,
    )

    # Auth: prefer derive (más robusto ante cambios/bugs de keys antiguas)
    if cfg.clob_api_key and cfg.clob_api_secret and cfg.clob_api_passphrase:
        api_creds = ApiCreds(
            api_key=cfg.clob_api_key,
            api_secret=cfg.clob_api_secret,
            api_passphrase=cfg.clob_api_passphrase,
        )
        client.set_api_creds(api_creds)
    else:
        client.set_api_creds(client.create_or_derive_api_creds())

    return client


def place_dual_orders_for_market(cfg: Config, market: dict) -> dict[str, Any]:
    slug = market.get("slug", "?")

    if market.get("closed") is True:
        raise RuntimeError(f"Market cerrado: {slug}")
    if market.get("acceptingOrders") is False:
        raise RuntimeError(f"Market no acepta órdenes: {slug}")

    token_up, token_down = _parse_clob_token_ids(market)[0:2]

    # info útil de Gamma (debug/consistencia)
    tick_size = str(market.get("orderPriceMinTickSize", "0.01"))
    neg_risk = bool(market.get("negRisk", False))
    maker_fee_bps = int(market.get("makerBaseFee", 0))  # en tus logs: 1000

    meta = {"tick_size": tick_size, "neg_risk": neg_risk, "maker_fee_bps": maker_fee_bps}

    if cfg.dry_run:
        return {
            "slug": slug,
            "dry_run": True,
            "meta": meta,
            "up": {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up},
            "down": {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down},
        }

    client = _mk_client(cfg)

    # Nota: OrderArgs evoluciona por versiones. En 0.34.x, suele aceptar fee_rate_bps.
    # Para que no “reviente” si cambia, lo ponemos de forma segura.
    def _order_args(**kwargs):
        # Filtra campos no soportados por la clase actual
        ann = getattr(OrderArgs, "__annotations__", {}) or {}
        safe = {k: v for k, v in kwargs.items() if k in ann or not ann}
        return OrderArgs(**safe)

    up_order = _order_args(
        token_id=token_up,
        price=cfg.price_up,
        size=cfg.size_up,
        side=BUY,
        fee_rate_bps=maker_fee_bps,
    )
    down_order = _order_args(
        token_id=token_down,
        price=cfg.price_down,
        size=cfg.size_down,
        side=BUY,
        fee_rate_bps=maker_fee_bps,
    )

    signed_up = client.create_order(up_order)
    signed_down = client.create_order(down_order)

    up_resp = client.post_order(signed_up, OrderType.GTC)
    down_resp = client.post_order(signed_down, OrderType.GTC)

    return {"slug": slug, "meta": meta, "up": up_resp, "down": down_resp}
