from __future__ import annotations

from typing import Any
import inspect
import json

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions

from tool.config import Config


def _apply_py_clob_client_hmac_patch() -> None:
    """
    Parche runtime para el bug del issue #249:
    build_hmac_signature() doble-encodeaba body si ya era JSON string y eso rompe L2 => 401.
    Fuente: issue #249.  [oai_citation:2‡GitHub](https://github.com/Polymarket/py-clob-client/issues/249)
    """
    try:
        from py_clob_client.signing import hmac as hmac_mod
    except Exception:
        return

    if getattr(hmac_mod, "__patched_no_double_encode__", False):
        return

    orig = getattr(hmac_mod, "build_hmac_signature", None)
    if orig is None:
        return

    def build_hmac_signature_fixed(secret: str, method: str, request_path: str, body: Any | None, timestamp: str) -> str:
        import hmac
        import hashlib

        message = f"{timestamp}{method.upper()}{request_path}"
        if body:
            if isinstance(body, str):
                message += body
            else:
                message += json.dumps(body, separators=(",", ":"))

        sig = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        return sig

    hmac_mod.build_hmac_signature = build_hmac_signature_fixed  # type: ignore
    hmac_mod.__patched_no_double_encode__ = True


def _parse_clob_token_ids(market: dict) -> list[str]:
    v = market.get("clobTokenIds")
    if v is None:
        raise RuntimeError("Market no trae clobTokenIds")

    if isinstance(v, list):
        return [str(x) for x in v]

    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            arr = json.loads(s)
            if not isinstance(arr, list) or len(arr) < 2:
                raise RuntimeError("clobTokenIds no tiene 2 elementos")
            return [str(x) for x in arr]

    raise RuntimeError(f"Formato clobTokenIds inesperado: {type(v)}")


def _mk_client(cfg: Config) -> ClobClient:
    _apply_py_clob_client_hmac_patch()

    client = ClobClient(
        host=cfg.clob_host.rstrip("/"),
        chain_id=cfg.chain_id,
        key=cfg.private_key,              # SDK actual usa 'key'
        signature_type=cfg.signature_type,
        funder=cfg.funder_address,
    )

    # Set creds
    if cfg.use_derived_creds:
        # Esto crea o deriva las creds L2 desde tu key (y las setea)
        derived = client.create_or_derive_api_creds()
        client.set_api_creds(derived)
    else:
        api_creds = ApiCreds(
            api_key=cfg.clob_api_key,
            api_secret=cfg.clob_api_secret,
            api_passphrase=cfg.clob_api_passphrase,
        )
        client.set_api_creds(api_creds)

    return client


def _create_order_compat(client: ClobClient, order: OrderArgs, tick_size: str, neg_risk: bool):
    """
    Wrapper: distintas versiones del SDK cambian la firma de create_order().
    Intentamos varios patrones.
    """
    opts = CreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

    # Intento 1 (SDK newer): create_order(order, opts)
    try:
        return client.create_order(order, opts)
    except TypeError:
        pass

    # Intento 2: create_order(order) (mínimo)
    try:
        return client.create_order(order)
    except TypeError:
        pass

    # Intento 3 (SDK older): create_order(order, OrderType.GTC, opts)
    return client.create_order(order, OrderType.GTC, opts)


def _post_order_compat(client: ClobClient, signed_order: Any):
    """
    Wrapper: distintas versiones del SDK cambian la firma de post_order().
    """
    try:
        return client.post_order(signed_order)
    except TypeError:
        # Algunas versiones piden orderType separado
        return client.post_order(signed_order, OrderType.GTC)


def place_dual_orders_for_market(cfg: Config, market: dict) -> dict[str, Any]:
    slug = market.get("slug", "?")

    if market.get("closed") is True:
        raise RuntimeError(f"Market cerrado: {slug}")
    if market.get("acceptingOrders") is False:
        raise RuntimeError(f"Market no acepta órdenes: {slug}")

    token_up, token_down = _parse_clob_token_ids(market)[0:2]

    tick_size = str(market.get("orderPriceMinTickSize", "0.01"))
    neg_risk = bool(market.get("negRisk", False))
    maker_fee_bps = int(market.get("makerBaseFee", 0))  # en tu debug: 1000

    if cfg.dry_run:
        return {
            "slug": slug,
            "dry_run": True,
            "meta": {"tick_size": tick_size, "neg_risk": neg_risk, "maker_fee_bps": maker_fee_bps},
            "up": {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up},
            "down": {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down},
        }

    client = _mk_client(cfg)

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

    signed_up = _create_order_compat(client, up_order, tick_size, neg_risk)
    signed_down = _create_order_compat(client, down_order, tick_size, neg_risk)

    up_resp = _post_order_compat(client, signed_up)
    down_resp = _post_order_compat(client, signed_down)

    return {
        "slug": slug,
        "meta": {"tick_size": tick_size, "neg_risk": neg_risk, "maker_fee_bps": maker_fee_bps},
        "up": up_resp,
        "down": down_resp,
    }
