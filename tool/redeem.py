from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytz

from tool.config import Config


def _mk_client(cfg: Config):
    """
    Crea un ClobClient. Soporta dos modos:
    - USE_DERIVED_CREDS=false: usa CLOB_API_KEY/SECRET/PASSPHRASE
    - USE_DERIVED_CREDS=true: intenta derivar creds si el SDK lo soporta
      (y si no lo soporta, sigue y fallará con 401, pero lo verás en logs).
    """
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    # 1) construir cliente con o sin creds
    creds_obj = None
    if not cfg.use_derived_creds:
        # exigimos que existan
        if not (cfg.clob_api_key and cfg.clob_api_secret and cfg.clob_api_passphrase):
            raise RuntimeError("Faltan CLOB_API_KEY / CLOB_API_SECRET / CLOB_API_PASSPHRASE y USE_DERIVED_CREDS=false")
        creds_obj = ApiCreds(
            api_key=cfg.clob_api_key,
            api_secret=cfg.clob_api_secret,
            api_passphrase=cfg.clob_api_passphrase,
        )
    else:
        # derived creds: si el usuario también puso creds manuales, las usamos de fallback
        if cfg.clob_api_key and cfg.clob_api_secret and cfg.clob_api_passphrase:
            creds_obj = ApiCreds(
                api_key=cfg.clob_api_key,
                api_secret=cfg.clob_api_secret,
                api_passphrase=cfg.clob_api_passphrase,
            )

    client = ClobClient(
        host=cfg.clob_host.rstrip("/"),
        chain_id=cfg.chain_id,
        key=cfg.private_key,              # <- el SDK usa 'key'
        creds=creds_obj,                  # <- puede ser None si derived
        signature_type=cfg.signature_type,
        funder=cfg.funder_address,
    )

    # 2) intentar derivar creds si aplica
    if cfg.use_derived_creds:
        # Distintas versiones del SDK han tenido nombres distintos.
        # Probamos varios sin romper si no existen.
        derived = None
        for meth_name in ("derive_api_creds", "create_or_derive_api_creds", "derive_creds", "get_or_derive_api_creds"):
            meth = getattr(client, meth_name, None)
            if callable(meth):
                try:
                    derived = meth()
                    break
                except Exception as e:
                    print(f"[redeem][WARN] {meth_name}() failed: {e}")

        if derived is not None:
            # Algunos devuelven ApiCreds, otros dict/tuple. Si es ApiCreds, lo seteamos.
            try:
                client.creds = derived
                print("[redeem] derived creds applied to client")
            except Exception:
                # si el SDK no deja setear .creds, al menos queda el log
                print("[redeem][WARN] derived creds obtained but couldn't assign to client.creds")

    return client


def _methods_snapshot(client) -> dict[str, list[str]]:
    """
    Debug: qué métodos útiles expone el cliente.
    """
    wants = [
        "get_trades", "get_orders", "get_positions",
        "redeem", "redeem_position", "redeem_market",
        "claim", "claim_winnings",
    ]
    present = [m for m in wants if callable(getattr(client, m, None))]
    all_redeemish = [m for m in dir(client) if "redeem" in m.lower() or "claim" in m.lower()]
    return {"present": present, "redeemish": all_redeemish}


def redeem_last_hours(cfg: Config, lookback_hours: int, anchor_utc: datetime | None = None) -> None:
    """
    Intenta identificar actividad reciente y (si el SDK lo soporta) ejecutar redeem/claim.

    IMPORTANTE:
    - En muchas versiones del py_clob_client NO existe un método de redeem directo.
      En ese caso este módulo solo podrá "detectar" y loguear, no cobrar.
    - Este redeem no crashea: loguea y termina.
    """
    end_utc = anchor_utc or datetime.now(tz=pytz.UTC)
    start_utc = end_utc - timedelta(hours=lookback_hours)

    print("[redeem] START")
    print(f"[redeem] lookback_hours={lookback_hours} start_utc={start_utc.isoformat()} end_utc={end_utc.isoformat()}")

    try:
        client = _mk_client(cfg)
    except Exception as e:
        print(f"[redeem][FAIL] cannot create client: {e}")
        print("[redeem] END")
        return

    snap = _methods_snapshot(client)
    print(f"[redeem] client methods present: {snap['present']}")
    print(f"[redeem] redeem-like methods available: {snap['redeemish'][:20]}")

    # 1) intenta obtener trades u orders para ver si hay algo
    items: list[dict[str, Any]] = []

    # Prefer trades
    if callable(getattr(client, "get_trades", None)):
        try:
            print("[redeem] trying get_trades()")
            resp = client.get_trades()
            # resp puede ser list o dict con 'data'
            if isinstance(resp, list):
                items = resp
            elif isinstance(resp, dict):
                items = resp.get("data") or resp.get("trades") or []
            print(f"[redeem] get_trades -> {len(items)} items")
        except Exception as e:
            print(f"[redeem][WARN] get_trades() failed: {e}")

    # Fallback orders
    if not items and callable(getattr(client, "get_orders", None)):
        try:
            print("[redeem] trying get_orders()")
            resp = client.get_orders()
            if isinstance(resp, list):
                items = resp
            elif isinstance(resp, dict):
                items = resp.get("data") or resp.get("orders") or []
            print(f"[redeem] get_orders -> {len(items)} items")
        except Exception as e:
            print(f"[redeem][WARN] get_orders() failed: {e}")

    if not items:
        print("[redeem] no items found to inspect; nothing to redeem (or auth/SDK doesn't expose it).")
        print("[redeem] END")
        return

    # 2) filtra items por rango temporal si traen timestamps
    def _parse_dt(x: Any) -> datetime | None:
        if not x:
            return None
        if isinstance(x, (int, float)):
            # epoch seconds
            try:
                return datetime.fromtimestamp(float(x), tz=pytz.UTC)
            except Exception:
                return None
        if isinstance(x, str):
            s = x.strip()
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(pytz.UTC)
            except Exception:
                return None
        return None

    recent = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # posibles campos
        dt = _parse_dt(it.get("createdAt") or it.get("timestamp") or it.get("time") or it.get("created_at"))
        if dt is None:
            # si no tiene fecha, lo ignoramos para el filtro temporal
            continue
        if start_utc <= dt <= end_utc:
            recent.append(it)

    print(f"[redeem] items in window: {len(recent)} (out of {len(items)})")

    # 3) intentar redeem si existe algún método directo
    redeem_fn = None
    for candidate in ("redeem", "claim_winnings", "claim", "redeem_position", "redeem_market"):
        f = getattr(client, candidate, None)
        if callable(f):
            redeem_fn = f
            print(f"[redeem] found redeem function: {candidate}")
            break

    if redeem_fn is None:
        print("[redeem][INFO] SDK doesn't expose a redeem/claim method here. Nothing to execute automatically.")
        print("[redeem] END")
        return

    # 4) ejecutar redeem de forma conservadora: llamada sin args si lo permite
    # (si tu SDK requiere token_id/market_id, lo veríamos aquí y lo adaptamos con tu output)
    try:
        print("[redeem] attempting redeem call (no-args)")
        out = redeem_fn()
        print(f"[redeem] redeem result: {out}")
    except TypeError as te:
        print(f"[redeem][WARN] redeem requires arguments in this SDK version: {te}")
        print("[redeem][INFO] Envíame el signature de ese método (inspect.signature) y lo ajusto a tu versión.")
    except Exception as e:
        print(f"[redeem][FAIL] redeem call failed: {e}")

    print("[redeem] END")


def maybe_auto_redeem(cfg: Config) -> None:
    """
    Ejecuta redeem si AUTO_REDEEM=true.
    La ventana de lookback se ancla a:
    - REDEEM_ANCHOR=window_end -> anchor = WINDOW_END (local Madrid -> UTC)
    - REDEEM_ANCHOR=now -> anchor = ahora UTC
    """
    if not getattr(cfg, "auto_redeem", False):
        return

    lookback = int(getattr(cfg, "redeem_lookback_hours", 12))

    anchor_utc = None
    anchor_mode = getattr(cfg, "redeem_anchor", "window_end")

    if anchor_mode == "window_end":
        try:
            dt_local = cfg.parse_local_dt(cfg.window_end_local)
            anchor_utc = dt_local.astimezone(pytz.UTC)
            print(f"[redeem] anchor=window_end window_end_local={cfg.window_end_local} anchor_utc={anchor_utc.isoformat()}")
        except Exception as e:
            print(f"[redeem][WARN] cannot anchor to window_end, fallback to now. reason={e}")
            anchor_utc = None
    else:
        print("[redeem] anchor=now")

    redeem_last_hours(cfg, lookback_hours=lookback, anchor_utc=anchor_utc)
