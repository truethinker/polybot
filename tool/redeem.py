from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional, Any

import os
import requests

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from tool.config import Config

try:
    from web3 import Web3
except Exception:  # pragma: no cover
    Web3 = None  # type: ignore


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
    # Backward compatible entry point.
    # If REDEEM_ONCHAIN=true -> executes on-chain redeem with the EOA (PRIVATE_KEY).
    # Otherwise keeps the previous behaviour (trade detection only).
    if getattr(cfg, "redeem_onchain", False):
        redeem_onchain_last_hours(cfg, lookback_hours)
        return

    print("[redeem] START (detect-only)")

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


# =====================================
# ON-CHAIN REDEEM (EOA)
# =====================================

_CT_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "collateralToken", "type": "address"},
            {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y")


def _data_api_url(cfg: Config) -> str:
    return (cfg.data_api_url or "https://data-api.polymarket.com").rstrip("/")


def _fetch_positions(cfg: Config) -> list[dict]:
    """Fetch wallet positions from Polymarket Data API (best-effort)."""
    base = f"{_data_api_url(cfg)}/positions"
    limit = 500
    offset = 0
    out: list[dict] = []

    while True:
        url = (
            f"{base}?user={cfg.funder_address}"
            f"&redeemable=true&sizeThreshold=0&limit={limit}&offset={offset}"
        )
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected positions payload: {type(data)}")

        if offset == 0:
            keys = list(data[0].keys())[:30] if data else []
            print(f"[redeem][positions] page0 count={len(data)} sampleKeys={','.join(keys)}")
        else:
            print(f"[redeem][positions] offset={offset} count={len(data)}")

        out.extend(data)
        if len(data) < limit:
            break
        offset += limit
        if offset > 5000:
            break

    return out


def _parse_iso(v: Any) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        # seconds
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    if isinstance(v, str):
        try:
            s = v.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None


def _position_timestamp(pos: dict) -> Optional[datetime]:
    for k in (
        "resolvedAt",
        "marketEndTime",
        "eventEndTime",
        "endTime",
        "marketStartTime",
        "eventStartTime",
        "startTime",
        "startDate",
        "createdAt",
        "updatedAt",
    ):
        dt = _parse_iso(pos.get(k))
        if dt:
            return dt
    return None


def _to_usd(pos: dict) -> float:
    v = pos.get("redeemable")
    if v is None:
        v = pos.get("redeemableValue")
    if v is None:
        v = pos.get("redeemable_value")
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _pick_redeemables(cfg: Config, positions: list[dict], lookback_hours: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=int(lookback_hours))
    out: list[dict] = []
    for p in positions:
        usd = _to_usd(p)
        is_redeemable = bool(p.get("isRedeemable") is True or usd > 0)
        if not is_redeemable:
            continue
        if usd < float(getattr(cfg, "min_redeemable_usd", 0) or 0):
            continue
        ts = _position_timestamp(p)
        if ts and ts < cutoff:
            continue

        condition_id = p.get("conditionId") or p.get("condition_id") or p.get("condition")
        index_set = p.get("indexSet") or p.get("index_set") or p.get("index")
        if not condition_id or index_set is None:
            continue

        out.append(
            {
                "conditionId": str(condition_id),
                "indexSet": str(index_set),
                "redeemableUsd": usd,
                "ts": ts.isoformat() if ts else None,
                "marketSlug": p.get("marketSlug") or p.get("slug") or p.get("market") or p.get("marketId"),
            }
        )

    return out


def _mk_web3(cfg: Config) -> Web3:
    if Web3 is None:
        raise RuntimeError("web3 is not installed. Did you install requirements.txt?")
    if not cfg.rpc_url:
        raise RuntimeError("RPC_URL is required for on-chain redeem")
    w3 = Web3(Web3.HTTPProvider(cfg.rpc_url, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise RuntimeError("Could not connect to RPC_URL")
    return w3


def _gas_params(w3: Web3) -> dict:
    """Best-effort gas params supporting both legacy and EIP-1559."""
    # If node supports baseFeePerGas, use EIP-1559.
    try:
        block = w3.eth.get_block("latest")
        base_fee = block.get("baseFeePerGas")
        if base_fee is not None:
            prio_gwei = float(os.getenv("MAX_PRIORITY_FEE_GWEI", "30"))
            prio = w3.to_wei(prio_gwei, "gwei")
            max_fee = int(base_fee) * 2 + int(prio)
            return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": int(prio)}
    except Exception:
        pass

    # Fallback legacy gasPrice.
    gas_price = w3.eth.gas_price
    bump = float(os.getenv("GAS_PRICE_BUMP", "1.2"))
    return {"gasPrice": int(gas_price * bump)}


def redeem_onchain_last_hours(cfg: Config, lookback_hours: int) -> None:
    print("[redeem] START (on-chain)")
    print(f"[redeem] lookback_hours={lookback_hours} min_redeemable_usd={cfg.min_redeemable_usd}")

    positions = _fetch_positions(cfg)
    redeemables = _pick_redeemables(cfg, positions, lookback_hours)

    if not redeemables:
        print(
            "[redeem] No hay posiciones redeemables en Data API (o no hay shares en wallet). "
            "OJO: si compraste en CLOB, es posible que las shares estén en el exchange y necesites WITHDRAW a tu wallet antes de poder hacer redeem."
        )
        print("[redeem] END")
        return

    print(f"[redeem] redeemables={len(redeemables)}")
    for r in redeemables[:50]:
        t = f" ts={r['ts']}" if r.get("ts") else ""
        s = f" slug={r['marketSlug']}" if r.get("marketSlug") else ""
        print(
            f"[redeem][pos] conditionId={r['conditionId']} indexSet={r['indexSet']} redeemableUsd~{r['redeemableUsd']}{t}{s}"
        )
    if len(redeemables) > 50:
        print(f"[redeem] (+{len(redeemables)-50} más)")

    if cfg.dry_run or _bool_env("DRY_RUN", "false"):
        print("[redeem] DRY_RUN=true -> no envío transacciones.")
        print("[redeem] END")
        return

    w3 = _mk_web3(cfg)
    acct = w3.eth.account.from_key(cfg.private_key)
    if acct.address.lower() != cfg.funder_address.lower():
        print(
            f"[redeem][WARN] FUNDER_ADDRESS ({cfg.funder_address}) no coincide con address derivada de PRIVATE_KEY ({acct.address}). "
            "Continuo usando la PRIVATE_KEY para firmar (EOA)."
        )

    ct_addr = w3.to_checksum_address(cfg.conditional_tokens_address)
    collateral_addr = w3.to_checksum_address(cfg.collateral_token_address)
    ct = w3.eth.contract(address=ct_addr, abi=_CT_ABI)

    # Group by conditionId to batch indexSets per tx
    grouped: dict[str, set[int]] = {}
    for r in redeemables:
        cid = str(r["conditionId"])
        idx_raw = str(r["indexSet"]).strip()
        try:
            idx = int(idx_raw, 0)  # handles "0x.." and decimal
        except Exception:
            idx = int(float(idx_raw))
        grouped.setdefault(cid, set()).add(idx)

    nonce = w3.eth.get_transaction_count(acct.address)
    sent = 0
    for cid, idxs in grouped.items():
        idx_list = sorted(list(idxs))
        try:
            tx = ct.functions.redeemPositions(
                collateral_addr,
                "0x" + "00" * 32,  # parentCollectionId = bytes32(0)
                cid,
                idx_list,
            ).build_transaction(
                {
                    "from": acct.address,
                    "nonce": nonce,
                    "chainId": int(cfg.chain_id),
                    **_gas_params(w3),
                }
            )
            # estimate gas and add a buffer
            try:
                est = w3.eth.estimate_gas(tx)
                tx["gas"] = int(est * 1.25)
            except Exception:
                tx.setdefault("gas", 600_000)

            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            sent += 1
            print(f"[redeem][tx] sent conditionId={cid} indexSets={idx_list} hash={tx_hash.hex()}")

            confs = int(getattr(cfg, "redeem_wait_confirmations", 1) or 1)
            if confs > 0:
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
                status = receipt.get("status")
                if status != 1:
                    print(f"[redeem][tx][FAIL] status={status} hash={tx_hash.hex()}")
                else:
                    print(f"[redeem][tx][OK] block={receipt.get('blockNumber')} hash={tx_hash.hex()}")

            nonce += 1
        except Exception as e:
            print(f"[redeem][tx][FAIL] conditionId={cid}: {e}")
            nonce += 1

    print(f"[redeem] submitted_txs={sent} groups={len(grouped)}")
    print("[redeem] END")
