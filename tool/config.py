import os
from dataclasses import dataclass
from datetime import datetime
import pytz


@dataclass
class Config:
    gamma_host: str
    clob_host: str
    funder_address: str
    signature_type: int

    private_key: str
    clob_api_key: str
    clob_api_secret: str
    clob_api_passphrase: str
    chain_id: int

    series_slug: str

    window_start_local: str
    window_end_local: str

    price_up: float
    size_up: float
    price_down: float
    size_down: float

    dry_run: bool
    max_markets: int

    # redeem
    auto_redeem: bool
    redeem_lookback_hours: int
    use_derived_creds: bool

    # redeem (on-chain, EOA)
    redeem_onchain: bool
    rpc_url: str
    data_api_url: str
    conditional_tokens_address: str
    collateral_token_address: str
    min_redeemable_usd: float
    redeem_wait_confirmations: int

    @property
    def tz(self):
        return pytz.timezone("Europe/Madrid")

    def parse_local_dt(self, s: str) -> datetime:
        naive = datetime.fromisoformat(s)
        return self.tz.localize(naive)

    def window_start_utc_iso(self) -> str:
        dt_utc = self.parse_local_dt(self.window_start_local).astimezone(pytz.UTC)
        return dt_utc.isoformat().replace("+00:00", "Z")

    def window_end_utc_iso(self) -> str:
        dt_utc = self.parse_local_dt(self.window_end_local).astimezone(pytz.UTC)
        return dt_utc.isoformat().replace("+00:00", "Z")


def _getenv(name: str, default: str | None = None, required: bool = False) -> str:
    v = os.getenv(name, default)
    if required and (v is None or v.strip() == ""):
        raise RuntimeError(f"Falta variable de entorno requerida: {name}")
    return v.strip() if isinstance(v, str) else v


def load_config() -> Config:
    return Config(
        gamma_host=_getenv("GAMMA_HOST", "https://gamma-api.polymarket.com"),
        clob_host=_getenv("CLOB_HOST", "https://clob.polymarket.com"),
        funder_address=_getenv("FUNDER_ADDRESS", required=True),
        signature_type=int(_getenv("SIGNATURE_TYPE", "0")),

        private_key=_getenv("PRIVATE_KEY", required=True),
        clob_api_key=_getenv("CLOB_API_KEY", ""),
        clob_api_secret=_getenv("CLOB_API_SECRET", ""),
        clob_api_passphrase=_getenv("CLOB_API_PASSPHRASE", ""),
        chain_id=int(_getenv("CHAIN_ID", "137")),

        series_slug=_getenv("SERIES_SLUG", "btc-up-or-down-5m"),

        window_start_local=_getenv("WINDOW_START", required=True),
        window_end_local=_getenv("WINDOW_END", required=True),

        price_up=float(_getenv("PRICE_UP", required=True)),
        size_up=float(_getenv("SIZE_UP", required=True)),
        price_down=float(_getenv("PRICE_DOWN", required=True)),
        size_down=float(_getenv("SIZE_DOWN", required=True)),

        dry_run=_getenv("DRY_RUN", "true").lower() in ("1", "true", "yes"),
        max_markets=int(_getenv("MAX_MARKETS", "200")),

        auto_redeem=_getenv("AUTO_REDEEM", "false").lower() in ("1", "true", "yes"),
        redeem_lookback_hours=int(_getenv("REDEEM_LOOKBACK_HOURS", "12")),
        use_derived_creds=_getenv("USE_DERIVED_CREDS", "false").lower() in ("1", "true", "yes"),

        # on-chain redeem (defaults target Polygon)
        redeem_onchain=_getenv("REDEEM_ONCHAIN", "false").lower() in ("1", "true", "yes"),
        rpc_url=_getenv("RPC_URL", ""),
        data_api_url=_getenv("DATA_API_URL", "https://data-api.polymarket.com"),
        conditional_tokens_address=_getenv(
            "CONDITIONAL_TOKENS_ADDRESS",
            "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",  # Polygon ConditionalTokens (public examples)
        ),
        collateral_token_address=_getenv(
            "COLLATERAL_TOKEN_ADDRESS",
            "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e (Polygon)
        ),
        min_redeemable_usd=float(_getenv("MIN_REDEEMABLE_USD", "0")),
        redeem_wait_confirmations=int(_getenv("REDEEM_WAIT_CONFIRMATIONS", "1")),
    )
