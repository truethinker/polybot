from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from tool.config import Config


# =====================================
# CLIENT (IDÉNTICO AL QUE METE ÓRDENES)
# =====================================

def _mk_client(cfg: Config) -> ClobClient:
    client = ClobClient(
        host=cfg.clob_host.rstrip("/"),
        chain_id=cfg.chain_id,
        key=cfg.private_key,
        signature_type=cfg.signature_type,
        funder=cfg.funder_address,
    )

    if cfg.use_derived_creds:
        client.set_api_creds(client.create_or_derive_api_creds())
        print("[redeem] derived creds applied")
    else:
        client.set_api_creds(
            ApiCreds(
                api_key=cfg.clob_api_key,
                api_secret=cfg.clob_api_secret,
                api_passphrase=cfg.clob_api_passphrase,
            )
        )
        print("[redeem] manual creds applied")

    return client


# =====================================
# TIMESTAMP PARSING ROBUSTO
# =====================================

def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None

    if isinstance(v, datetime):
        return v.astimezone(timezone.utc)

    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e12:  # ms
            return datetime.fromtimestamp(x / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(x, tz=timezone.utc)

    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            return _parse_dt(int(s))
        try:
            if s.endswith("Z"):
                s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    return None


# =====================================
# REDEEM LOGIC (SOLO DETECCIÓN)
# =====================================

def redeem_last_hours(cfg: Config, lookback_hours: int) -> None:
    print("[redeem] START")

    end_utc = datetime.fromisoformat(
        cfg.window_end_utc_iso().replace("Z", "+00:00")
    ).astimezone(timezone.utc)

    start_utc = end_utc - timedelta(hours=int(lookback_hours))

    print(f"[redeem] window: {start_utc} -> {end_utc}")

    client = _mk_client(cfg)

    try:
        trades = client.get_trades()
        if isinstance(trades, dict) and "data" in trades:
            trades = trades["data"]
        print(f"[redeem] trades fetched: {len(trades)}")
    except Exception as e:
        print(f"[redeem][FAIL] get_trades failed: {e}")
        print("[redeem] END")
        return

    if not trades:
        print("[redeem] no trades found")
        print("[redeem] END")
        return

    hits = []

    for t in trades:
        dt = _parse_dt(t.get("match_time")) or _parse_dt(t.get("last_update"))
        if not dt:
            continue

        if start_utc <= dt <= end_utc:
            hits.append((t, dt))

    print(f"[redeem] hits in window: {len(hits)}")

    for t, dt in hits[:20]:
        print(
            "[redeem][TRADE]",
            {
                "dt": dt.isoformat(),
                "market": t.get("market"),
                "outcome": t.get("outcome"),
                "side": t.get("side"),
                "price": t.get("price"),
                "size": t.get("size"),
                "status": t.get("status"),
            },
        )

    print("[redeem] END")
