from datetime import datetime, timedelta, timezone
from typing import Any
import inspect

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from tool.config import Config


# ==============================
# CLIENT CREATION
# ==============================

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
        print("[redeem] derived creds applied to client")
    else:
        client.set_api_creds(ApiCreds(
            api_key=cfg.clob_api_key,
            api_secret=cfg.clob_api_secret,
            api_passphrase=cfg.clob_api_passphrase,
        ))
        print("[redeem] manual creds applied to client")

    return client


# ==============================
# TIME PARSING (ROBUST)
# ==============================

def _to_utc_dt(v) -> datetime | None:
    if v is None:
        return None

    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    if isinstance(v, (int, float)):
        x = float(v)
        if x > 10_000_000_000:  # probablemente ms
            x /= 1000.0
        return datetime.fromtimestamp(x, tz=timezone.utc)

    return None


def _trade_time_value(tr: dict):
    for k in (
        "created_at",
        "createdAt",
        "timestamp",
        "tradeTime",
        "time",
        "t",
        "blockTimestamp",
        "updatedAt",
        "updated_at",
    ):
        if k in tr:
            return tr.get(k)
    return None


def _anchor_end_utc(cfg: Config) -> datetime:
    try:
        return datetime.fromisoformat(
            cfg.window_end_utc_iso().replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


# ==============================
# MAIN REDEEM FUNCTION
# ==============================

def redeem_last_hours(cfg: Config):
    print("[redeem] START")

    lookback_hours = cfg.redeem_lookback_hours
    end_utc = _anchor_end_utc(cfg)
    start_utc = end_utc - timedelta(hours=lookback_hours)

    print(f"[redeem] lookback_hours={lookback_hours} start_utc={start_utc} end_utc={end_utc}")

    client = _mk_client(cfg)

    print("[redeem] client methods present:",
          [m for m in dir(client) if m in ("get_trades", "get_orders")])

    trades = []
    try:
        print("[redeem] trying get_trades()")
        trades = client.get_trades()
        print(f"[redeem] get_trades -> {len(trades)} items")
    except Exception as e:
        print(f"[redeem][WARN] get_trades() failed: {e}")

    if not trades:
        print("[redeem] no trades returned.")
        print("[redeem] END")
        return

    # DEBUG sample keys
    print("[redeem] sample trade keys:", sorted(list(trades[0].keys())))

    items_in_window = []
    min_dt = None
    max_dt = None

    for tr in trades:
        raw = _trade_time_value(tr)
        dt = _to_utc_dt(raw)

        if not dt:
            continue

        if min_dt is None or dt < min_dt:
            min_dt = dt
        if max_dt is None or dt > max_dt:
            max_dt = dt

        if start_utc <= dt <= end_utc:
            items_in_window.append(tr)

    print(f"[redeem] parsed trades dt range: min={min_dt} max={max_dt}")
    print(f"[redeem] items in window: {len(items_in_window)} (out of {len(trades)})")

    if not items_in_window:
        print("[redeem] nothing in window.")
        print("[redeem] END")
        return

    print("[redeem][INFO] SDK doesn't expose direct redeem here.")
    print("[redeem] END")
