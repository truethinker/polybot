from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable, Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from tool.config import Config


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc_dt(v: Any) -> Optional[datetime]:
    """
    Convierte timestamps típicos del SDK a datetime UTC.

    Soporta:
    - ISO strings: "2026-02-21T16:22:36.173533Z"
    - ISO strings sin Z: "2026-02-21T16:22:36+00:00"
    - epoch seconds: 1700000000
    - epoch milliseconds: 1700000000000
    - strings numéricos: "1700000000" / "1700000000000"
    """
    if v is None:
        return None

    # datetime ya listo
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    # ints/floats epoch
    if isinstance(v, (int, float)):
        x = float(v)
        # heurística ms vs s
        if x > 1e12:  # ms
            return datetime.fromtimestamp(x / 1000.0, tz=timezone.utc)
        if x > 1e9:  # s
            return datetime.fromtimestamp(x, tz=timezone.utc)
        return None

    # strings: ISO o numéricas
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None

        # numérico
        if s.isdigit():
            x = float(s)
            if x > 1e12:
                return datetime.fromtimestamp(x / 1000.0, tz=timezone.utc)
            if x > 1e9:
                return datetime.fromtimestamp(x, tz=timezone.utc)
            return None

        # ISO
        try:
            # normaliza Z
            if s.endswith("Z"):
                s2 = s.replace("Z", "+00:00")
            else:
                s2 = s
            dt = datetime.fromisoformat(s2)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    return None


def _mk_client(cfg: Config) -> ClobClient:
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


def redeem_last_hours(cfg: Config, lookback_hours: int, *, anchor_end_utc: datetime | None = None) -> None:
    """
    NOTA: este módulo hoy NO 'cobra' porque el SDK CLOB no expone redeem/claim,
    pero SÍ debe:
      - localizar trades del wallet en la ventana
      - mostrarlos y dejar claro qué tokens/markets/outcomes están implicados
    """
    end_utc = anchor_end_utc or _utcnow()
    start_utc = end_utc - timedelta(hours=int(lookback_hours))

    print("[redeem] START")
    print(f"[redeem] lookback_hours={lookback_hours} start_utc={start_utc} end_utc={end_utc}")

    client = _mk_client(cfg)

    # 1) Traer trades
    trades = []
    try:
        # Muchos builds del SDK aceptan filtros opcionales; si falla, cae al básico
        trades = client.get_trades()  # type: ignore
        if isinstance(trades, dict) and "data" in trades:
            trades = trades["data"]
        if trades is None:
            trades = []
        print(f"[redeem] get_trades -> {len(trades)} items")
    except Exception as e:
        print(f"[redeem][WARN] get_trades() failed: {e}")
        trades = []

    if not trades:
        print("[redeem] no trades returned; nothing to inspect.")
        print("[redeem] END")
        return

    # 2) Debug: muestra 3 ejemplos crudos de match_time/last_update para ver formato real
    sample = trades[:3]
    for i, t in enumerate(sample):
        mt_raw = t.get("match_time")
        lu_raw = t.get("last_update")
        print(f"[redeem] sample[{i}] match_time={mt_raw!r} last_update={lu_raw!r} owner={t.get('owner')!r}")

    # 3) Parse dt usando match_time si existe, si no last_update
    parsed: list[tuple[dict, datetime]] = []
    for t in trades:
        dt = _as_utc_dt(t.get("match_time")) or _as_utc_dt(t.get("last_update"))
        if dt is None:
            continue
        parsed.append((t, dt))

    if not parsed:
        print("[redeem][WARN] Could not parse ANY trade timestamps. Check sample logs above.")
        print("[redeem] END")
        return

    dts = [dt for _, dt in parsed]
    print(f"[redeem] parsed trades dt range: min={min(dts)} max={max(dts)}")

    # 4) Filtra por ventana
    in_window = [(t, dt) for (t, dt) in parsed if start_utc <= dt <= end_utc]
    print(f"[redeem] items in window: {len(in_window)} (out of {len(parsed)})")

    if not in_window:
        print("[redeem] nothing in window.")
        print("[redeem] END")
        return

    # 5) (Opcional) filtra por tu address, por si vienen trades de otros owners
    addr = (cfg.funder_address or "").lower()
    filtered = []
    for t, dt in in_window:
        owner = (t.get("owner") or "").lower()
        maker = (t.get("maker_address") or "").lower()
        if addr and (addr in owner or addr == maker):
            filtered.append((t, dt))
    if filtered:
        in_window = filtered
        print(f"[redeem] items in window for funder_address={cfg.funder_address}: {len(in_window)}")

    # 6) Imprime resumen útil (lo que tú esperas “encontrar”)
    # Esto NO cobra, pero te lista lo que ocurrió ayer.
    for t, dt in in_window[:30]:
        print(
            "[redeem][HIT]",
            {
                "dt": dt.isoformat(),
                "market": t.get("market"),
                "asset_id": t.get("asset_id"),
                "outcome": t.get("outcome"),
                "side": t.get("side"),
                "price": t.get("price"),
                "size": t.get("size"),
                "status": t.get("status"),
                "tx": t.get("transaction_hash"),
            },
        )

    if len(in_window) > 30:
        print(f"[redeem] showing first 30 of {len(in_window)} hits")

    print("[redeem] END")
