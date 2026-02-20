from __future__ import annotations

import os
from datetime import datetime, timedelta
import pytz
from typing import Any, Iterable

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from tool.config import Config


def _mk_client(cfg: Config) -> ClobClient:
    """
    Cliente CLOB usando la misma configuración que usas para tradear.
    OJO: aquí no usamos nada "mágico": mismo host/chain/key/creds/funder/signature_type.
    """
    api_creds = ApiCreds(
        api_key=cfg.clob_api_key,
        api_secret=cfg.clob_api_secret,
        api_passphrase=cfg.clob_api_passphrase,
    )

    return ClobClient(
        host=cfg.clob_host.rstrip("/"),
        chain_id=cfg.chain_id,
        key=cfg.private_key,
        creds=api_creds,
        signature_type=cfg.signature_type,
        funder=cfg.funder_address,
    )


def redeem_last_hours(cfg: Config, lookback_hours: int = 12) -> None:
    """
    Intenta redimir (claim/redeem) mercados resueltos donde tengas payout pendiente
    en las últimas `lookback_hours`.
    La API exacta de py_clob_client ha cambiado varias veces, así que este método:
      - enumera posiciones / markets recientes
      - intenta llamar a un método de redeem/claim si existe
      - loguea todo lo que hace (o por qué no puede hacerlo)
    """
    client = _mk_client(cfg)

    now_utc = datetime.now(tz=pytz.UTC)
    start_utc = now_utc - timedelta(hours=int(lookback_hours))

    print(f"[redeem] lookback_hours={lookback_hours} start_utc={start_utc.isoformat()} now_utc={now_utc.isoformat()}", flush=True)

    # 1) Intento A: si el SDK trae método directo tipo redeem/claim…
    # (depende de la versión)
    possible_methods = [
        "redeem_positions",
        "redeem_position",
        "redeem",
        "claim",
        "claim_winnings",
        "redeem_market",
        "redeem_markets",
    ]

    available = [m for m in possible_methods if hasattr(client, m)]
    print(f"[redeem] client methods available for redeem: {available}", flush=True)

    # Si hay un método directo (raro pero ideal), lo intentamos sin romper nada.
    for m in available:
        fn = getattr(client, m)
        try:
            # Intento sin args primero (algunas versiones calculan por wallet)
            print(f"[redeem] trying {m}()", flush=True)
            res = fn()  # type: ignore
            print(f"[redeem][OK] {m}() -> {str(res)[:500]}", flush=True)
            return
        except TypeError:
            # necesita args: seguimos a estrategia B
            pass
        except Exception as e:
            print(f"[redeem][WARN] {m}() failed: {e}", flush=True)

    # 2) Estrategia B: listar fills/positions recientes y luego redeem por market si existe.
    # Vamos a intentar sacar "activity" o "positions". El SDK cambia nombres, así que probamos varios.
    list_candidates = [
        "get_positions",
        "get_user_positions",
        "get_portfolio",
        "get_balances",
        "get_trades",
        "get_fills",
        "get_orders",
    ]
    list_available = [m for m in list_candidates if hasattr(client, m)]
    print(f"[redeem] list candidates available: {list_available}", flush=True)

    items: list[dict[str, Any]] = []

    # Probamos por orden hasta que algo devuelva datos con pinta de mercados/tokens.
    for m in list_available:
        fn = getattr(client, m)
        try:
            print(f"[redeem] trying {m}()", flush=True)
            res = fn()  # type: ignore
            # Normalizamos a lista de dicts si podemos
            if isinstance(res, list):
                items = [x if isinstance(x, dict) else {"value": x} for x in res]
            elif isinstance(res, dict):
                # Algunas funciones devuelven {"data":[...]} o similar
                if "data" in res and isinstance(res["data"], list):
                    items = [x if isinstance(x, dict) else {"value": x} for x in res["data"]]
                else:
                    items = [res]
            else:
                items = [{"value": res}]
            print(f"[redeem] {m}() returned {len(items)} items", flush=True)
            break
        except Exception as e:
            print(f"[redeem][WARN] {m}() failed: {e}", flush=True)

    if not items:
        print("[redeem] no items found to inspect; nothing to redeem (or SDK doesn't expose it).", flush=True)
        return

    # Intentamos extraer market identifiers de los items (muy variable).
    market_ids = set()
    for it in items:
        for key in ("market", "market_id", "marketId", "condition_id", "conditionId", "event_id", "eventId"):
            v = it.get(key)
            if isinstance(v, (str, int)) and str(v).strip():
                market_ids.add(str(v))

        # Algunas estructuras: it["market"]["conditionId"] etc
        mobj = it.get("market")
        if isinstance(mobj, dict):
            for key in ("condition_id", "conditionId", "id", "marketId"):
                v = mobj.get(key)
                if isinstance(v, (str, int)) and str(v).strip():
                    market_ids.add(str(v))

    market_ids = {mid for mid in market_ids if mid}
    print(f"[redeem] extracted market ids candidates: {list(market_ids)[:20]}", flush=True)

    # Si no hay ids, al menos no crasheamos.
    if not market_ids:
        print("[redeem] could not extract market ids from SDK output; nothing to redeem.", flush=True)
        return

    # Si existe redeem_market/claim por market id, lo intentamos.
    per_market_methods = [m for m in ("redeem_market", "claim_market", "redeem", "claim") if hasattr(client, m)]
    if not per_market_methods:
        print("[redeem] no per-market redeem method found in this SDK version.", flush=True)
        return

    fn = getattr(client, per_market_methods[0])
    ok = 0
    fail = 0
    for mid in list(market_ids):
        try:
            print(f"[redeem] trying {per_market_methods[0]}({mid})", flush=True)
            res = fn(mid)  # type: ignore
            ok += 1
            print(f"[redeem][OK] {mid} -> {str(res)[:500]}", flush=True)
        except Exception as e:
            fail += 1
            print(f"[redeem][FAIL] {mid}: {e}", flush=True)

    print(f"[redeem] done: ok={ok} fail={fail}", flush=True)
