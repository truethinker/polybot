from __future__ import annotations

"""Redeem (claim) winnings for resolved markets.

Why this module exists
----------------------
The CLOB Python SDK does not consistently expose a "redeem/claim" helper, and
API-key based endpoints can yield 401 even while posting orders works.

So we redeem *on-chain* via the Conditional Tokens Framework (CTF) contract.

Important
---------
- This sends Polygon transactions from the EOA corresponding to PRIVATE_KEY.
- The sender must hold the position tokens (ERC-1155) for the resolved market.
- If you traded using a different signer/funder in the past, use that key.

This module is intentionally defensive: it will only attempt a redeem when it
can (a) find a resolved market in the lookback window, (b) derive a winning
outcome, and (c) detect a positive balance of the winning token.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytz
import requests
from web3 import Web3

from tool.config import Config

# --- Minimal ABIs (only what we need) ---

ERC1155_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]

CTF_ABI = [
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


@dataclass
class RedeemCandidate:
    slug: str
    condition_id: str
    collateral: str
    winning_index: int
    winning_index_set: int
    token_id: str
    token_balance: int


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        raise RuntimeError(
            f"Gamma no devolvió JSON. Status={resp.status_code}, body={resp.text[:300]}"
        )


def _parse_listish(v: Any, default: list[str] | None = None) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(x) for x in arr]
        except Exception:
            pass
    return default or []


def _parse_int_listish(v: Any) -> list[int]:
    if isinstance(v, list):
        out: list[int] = []
        for x in v:
            try:
                out.append(int(x))
            except Exception:
                pass
        return out
    if isinstance(v, str):
        try:
            arr = json.loads(v)
            if isinstance(arr, list):
                return [int(x) for x in arr]
        except Exception:
            pass
    return []


def _market_is_resolved(m: dict) -> bool:
    # Gamma fields vary. Accept multiple.
    if m.get("resolved") is True or m.get("isResolved") is True:
        return True
    if m.get("closed") is True and (
        m.get("winner") or m.get("winningOutcome") or m.get("payoutNumerators")
    ):
        return True
    if m.get("resolution"):
        return True
    return False


def _extract_condition_id(m: dict) -> str | None:
    for k in ("conditionId", "condition_id", "conditionID"):
        v = m.get(k)
        if isinstance(v, str) and v.startswith("0x") and len(v) == 66:
            return v
    return None


def _pick_winning_index(m: dict, outcomes: list[str]) -> int | None:
    # 1) explicit winner
    winner = m.get("winner") or m.get("winningOutcome") or m.get("resolvedOutcome")
    if isinstance(winner, str) and outcomes:
        wl = winner.strip().lower()
        for i, o in enumerate(outcomes):
            if str(o).strip().lower() == wl:
                return i

    # 2) payout numerators
    pn = _parse_int_listish(m.get("payoutNumerators") or m.get("payout_numerators"))
    if pn:
        for i, x in enumerate(pn):
            if x and x > 0:
                return i

    return None


def _clob_token_ids(m: dict) -> list[str]:
    v = m.get("clobTokenIds") or m.get("clob_token_ids")
    ids = _parse_listish(v, default=[])
    if not ids and isinstance(m.get("clob"), dict):
        ids = _parse_listish(m["clob"].get("tokenIds"), default=[])
    return ids


def _collateral_from_market(m: dict, cfg: Config) -> str:
    for k in ("collateralAddress", "collateral", "collateralToken", "collateral_token"):
        v = m.get(k)
        if isinstance(v, str) and v.startswith("0x") and len(v) == 42:
            return v
    return cfg.collateral_token_address


def _slug_prefix(cfg: Config) -> str:
    # For btc-up-or-down-5m, the market slug uses btc-updown-5m-
    if cfg.series_slug == "btc-up-or-down-5m":
        return "btc-updown-5m-"
    return "btc-"


def _dt_to_z(dt: datetime) -> str:
    return dt.astimezone(pytz.UTC).isoformat().replace("+00:00", "Z")


def _anchor_end_utc(cfg: Config) -> datetime:
    anchor = (getattr(cfg, "redeem_anchor", "window_end") or "window_end").strip().lower()
    if anchor == "now":
        return datetime.now(tz=pytz.UTC)

    # default: anchor to WINDOW_END in Europe/Madrid
    try:
        return cfg.parse_local_dt(cfg.window_end_local).astimezone(pytz.UTC)
    except Exception:
        return datetime.now(tz=pytz.UTC)


def _gamma_markets_between(cfg: Config, start_min_z: str, start_max_z: str) -> list[dict]:
    """Query Gamma with server-side startDate filtering."""
    url = f"{cfg.gamma_host.rstrip('/')}/markets"
    limit = min(cfg.max_markets, 200)
    offset = 0
    out: list[dict] = []
    prefix = _slug_prefix(cfg)

    while True:
        params = {
            "limit": limit,
            "offset": offset,
            "order": "startDate",
            "ascending": "true",
            "archived": "false",
            # include closed for redeem
            "start_date_min": start_min_z,
            "start_date_max": start_max_z,
        }
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        page = _safe_json(r)

        if not isinstance(page, list):
            raise RuntimeError(f"Respuesta Gamma inesperada: {type(page)}")

        if page:
            first = page[0].get("startDate")
            last = page[-1].get("startDate")
            print(f"[redeem][Gamma page offset={offset}] first={first} last={last} count={len(page)}")
        else:
            print(f"[redeem][Gamma page offset={offset}] empty")

        for m in page:
            slug = str(m.get("slug", ""))
            if slug.startswith(prefix):
                out.append(m)

        if len(page) < limit:
            break
        offset += limit

    return out


def _build_candidates(cfg: Config, w3: Web3, owner: str, start_utc: datetime, end_utc: datetime) -> list[RedeemCandidate]:
    markets = _gamma_markets_between(cfg, _dt_to_z(start_utc), _dt_to_z(end_utc))
    if not markets:
        print("[redeem] no markets returned by Gamma in that startDate window.")
        return []

    # Polymarket uses an ERC-1155 for outcome positions.
    # In our config this is called conditional_tokens_address.
    erc1155 = w3.eth.contract(
        address=Web3.to_checksum_address(cfg.conditional_tokens_address), abi=ERC1155_ABI
    )

    cands: list[RedeemCandidate] = []

    for m in markets:
        slug = str(m.get("slug", "?"))
        if not _market_is_resolved(m):
            continue

        condition_id = _extract_condition_id(m)
        if not condition_id:
            continue

        outcomes = _parse_listish(m.get("outcomes"), default=[])
        win_idx = _pick_winning_index(m, outcomes)
        if win_idx is None:
            continue

        token_ids = _clob_token_ids(m)
        if len(token_ids) < (win_idx + 1):
            continue

        token_id = token_ids[win_idx]
        try:
            bal = int(
                erc1155.functions.balanceOf(Web3.to_checksum_address(owner), int(token_id)).call()
            )
        except Exception:
            continue

        if bal <= 0:
            continue

        index_set = 1 << int(win_idx)
        cands.append(
            RedeemCandidate(
                slug=slug,
                condition_id=condition_id,
                collateral=_collateral_from_market(m, cfg),
                winning_index=int(win_idx),
                winning_index_set=int(index_set),
                token_id=str(token_id),
                token_balance=int(bal),
            )
        )

    return cands


def redeem_last_hours(cfg: Config) -> None:
    """Redeem any resolved winning positions in the lookback window.

    Window is anchored to WINDOW_END (Europe/Madrid) by default.
    Set REDEEM_ANCHOR=now to anchor to current time.

    Required envs (in addition to your trading envs):
    - POLYGON_RPC_URL (recommended; otherwise your bot may fail to connect)
    """

    if not cfg.auto_redeem:
        return

    print("[redeem] START")

    end_utc = _anchor_end_utc(cfg)
    start_utc = end_utc - timedelta(hours=int(cfg.redeem_lookback_hours))
    print(
        f"[redeem] lookback_hours={cfg.redeem_lookback_hours} start_utc={start_utc.isoformat()} end_utc={end_utc.isoformat()} anchor={getattr(cfg, 'redeem_anchor', 'window_end')}"
    )

    if not cfg.polygon_rpc_url:
        print("[redeem][WARN] POLYGON_RPC_URL not set; cannot redeem on-chain.")
        print("[redeem] END")
        return

    w3 = Web3(Web3.HTTPProvider(cfg.polygon_rpc_url, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print("[redeem][WARN] could not connect to Polygon RPC; cannot redeem on-chain.")
        print("[redeem] END")
        return

    acct = w3.eth.account.from_key(cfg.private_key)
    owner = acct.address

    if cfg.funder_address and owner.lower() != cfg.funder_address.lower():
        print(
            f"[redeem][WARN] PRIVATE_KEY address ({owner}) != FUNDER_ADDRESS ({cfg.funder_address}). Redeem will use PRIVATE_KEY address."
        )

    cands = _build_candidates(cfg, w3, owner, start_utc, end_utc)
    print(f"[redeem] candidates found: {len(cands)}")

    if not cands:
        print("[redeem] nothing to redeem in that window.")
        print("[redeem] END")
        return

    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(cfg.conditional_tokens_address), abi=CTF_ABI
    )

    parent_collection_id = bytes.fromhex("00" * 32)

    for c in cands:
        print(
            f"[redeem] attempting redeem slug={c.slug} conditionId={c.condition_id} win_index={c.winning_index} token_id={c.token_id} bal={c.token_balance}"
        )
        try:
            tx = ctf.functions.redeemPositions(
                Web3.to_checksum_address(c.collateral),
                parent_collection_id,
                Web3.to_bytes(hexstr=c.condition_id),
                [int(c.winning_index_set)],
            ).build_transaction(
                {
                    "from": owner,
                    "nonce": w3.eth.get_transaction_count(owner),
                }
            )

            # EIP-1559 defaults (Polygon supports it)
            if "maxFeePerGas" not in tx:
                try:
                    base = w3.eth.gas_price
                except Exception:
                    base = 50_000_000_000  # 50 gwei
                tx["maxFeePerGas"] = int(base * 2)
                tx["maxPriorityFeePerGas"] = int(base * 0.25)

            try:
                tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
            except Exception:
                tx["gas"] = 350_000

            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            print(f"[redeem][SENT] {c.slug} tx={tx_hash.hex()}")
        except Exception as e:
            print(f"[redeem][FAIL] {c.slug}: {e}")

    print("[redeem] END")
