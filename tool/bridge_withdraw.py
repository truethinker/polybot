from __future__ import annotations

"""Bridge withdraw helper (Polymarket Bridge API).

This is NOT related to CLOB order placement.

What it does:
  1) Calls POST https://bridge.polymarket.com/withdraw to obtain a deposit address
     configured for a destination chain/token.
  2) Transfers USDC.e (Polygon) from your EOA to that deposit address on-chain.

Docs:
  - Bridge API: POST /withdraw returns addresses to send funds to. citeturn5view0
  - Bridge overview: bridge is a separate API https://bridge.polymarket.com citeturn2view0

Notes:
  - The Bridge API itself doesn't move your funds; you still must send USDC.e to the
    returned deposit address. citeturn4view0
"""

from dataclasses import dataclass


@dataclass
class BridgeWithdrawRequest:
    # Source Polymarket wallet address on Polygon (your EOA or proxy wallet address)
    address: str
    # Destination chain id as string, e.g. "1" for Ethereum
    to_chain_id: str
    # Destination token address on destination chain
    to_token_address: str
    # Recipient address on destination chain
    recipient_addr: str


def create_withdraw_addresses(
    req: BridgeWithdrawRequest,
    bridge_api: str = "https://bridge.polymarket.com",
) -> dict:
    import requests

    url = bridge_api.rstrip("/") + "/withdraw"
    payload = {
        "address": req.address,
        "toChainId": req.to_chain_id,
        "toTokenAddress": req.to_token_address,
        "recipientAddr": req.recipient_addr,
    }

    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"bridge /withdraw failed: {r.status_code} {r.text[:300]}")
    return r.json()


def to_raw_amount(amount: float, decimals: int = 6) -> int:
    # USDC-style default: 6 decimals
    return int(round(float(amount) * (10 ** int(decimals))))


def erc20_transfer(
    *,
    rpc_url: str,
    private_key: str,
    token_address: str,
    to_address: str,
    amount_raw: int,
    chain_id: int = 137,
    gas_price_bump: float = 1.2,
    max_priority_fee_gwei: float = 30,
    wait_confirmations: int = 1,
    dry_run: bool = False,
) -> str:
    """Transfers ERC20 tokens on Polygon.

    Returns tx hash hex.
    """
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise RuntimeError("could not connect to RPC_URL")

    acct = w3.eth.account.from_key(private_key)
    sender = acct.address

    token = Web3.to_checksum_address(token_address)
    to = Web3.to_checksum_address(to_address)

    ERC20_ABI = [
        {
            "name": "transfer",
            "type": "function",
            "stateMutability": "nonpayable",
            "inputs": [
                {"name": "to", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ],
            "outputs": [{"name": "", "type": "bool"}],
        },
        {
            "name": "balanceOf",
            "type": "function",
            "stateMutability": "view",
            "inputs": [{"name": "owner", "type": "address"}],
            "outputs": [{"name": "", "type": "uint256"}],
        },
    ]

    c = w3.eth.contract(address=token, abi=ERC20_ABI)
    bal = c.functions.balanceOf(sender).call()
    if bal < amount_raw:
        raise RuntimeError(f"insufficient token balance: have={bal} need={amount_raw}")

    nonce = w3.eth.get_transaction_count(sender)
    tx = c.functions.transfer(to, int(amount_raw)).build_transaction(
        {"from": sender, "nonce": nonce, "chainId": int(chain_id)}
    )

    # gas estimate
    try:
        est = w3.eth.estimate_gas(tx)
        tx["gas"] = int(est * 1.25)
    except Exception:
        tx["gas"] = 120_000

    # fees (best-effort)
    try:
        pending = w3.eth.get_block("pending")
        base_fee = pending.get("baseFeePerGas")
        if base_fee is not None:
            max_priority = int(float(max_priority_fee_gwei) * 1e9)
            max_fee = int((int(base_fee) + max_priority) * float(gas_price_bump))
            tx["maxPriorityFeePerGas"] = max_priority
            tx["maxFeePerGas"] = max_fee
            tx["type"] = 2
        else:
            gp = int(w3.eth.gas_price * float(gas_price_bump))
            tx["gasPrice"] = gp
            tx["type"] = 0
    except Exception:
        gp = int(w3.eth.gas_price * float(gas_price_bump))
        tx["gasPrice"] = gp
        tx["type"] = 0

    if dry_run:
        return "0xDRYRUN"

    signed = acct.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.rawTransaction)
    receipt = w3.eth.wait_for_transaction_receipt(
        txh, confirmations=int(wait_confirmations), timeout=300
    )
    if receipt.get("status") != 1:
        raise RuntimeError(f"erc20 transfer reverted: {txh.hex()}")
    return txh.hex()
