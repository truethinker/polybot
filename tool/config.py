import os
from dataclasses import dataclass
from datetime import datetime
import pytz


def _getenv(name: str, default: str | None = None, required: bool = False) -> str:
    v = os.getenv(name, default)
    if required and (v is None or str(v).strip() == ""):
        raise RuntimeError(f"Falta variable de entorno requerida: {name}")
    return v.strip() if isinstance(v, str) else str(v)


def _getenv_bool(name: str, default: str = "false") -> bool:
    return _getenv(name, default).strip().lower() in ("1", "true", "yes", "y")


@dataclass
class Config:
    gamma_host: str
    clob_host: str

    private_key: str
    clob_api_key: str
    clob_api_secret: str
    clob_api_passphrase: str

    chain_id: int
    funder_address: str
    signature_type: int
    use_derived_creds: bool

    series_slug: str

    window_start_local: str  # Europe/Madrid
    window_end_local: str    # Europe/Madrid

    price_up: float
    size_up: float
    price_down: float
    size_down: float

    dry_run: bool
    max_markets: int

    polygon_rpc_url: str
    auto_redeem: bool
    auto_redeem_wait_seconds: int

    @property
    def tz(self):
        return pytz.timezone("Europe/Madrid")

    def parse_local_dt(self, s: str) -> datetime:
        naive = datetime.fromisoformat(s)
        return self.tz.localize(naive)

    def validate(self) -> None:
        ws = self.parse_local_dt(self.window_start_local)
        we = self.parse_local_dt(self.window_end_local)
        if we <= ws:
            raise RuntimeError("WINDOW_END debe ser posterior a WINDOW_START (en hora local Europe/Madrid).")

        if not self.funder_address.lower().startswith("0x") or len(self.funder_address) != 42:
            raise RuntimeError("FUNDER_ADDRESS no parece una address EVM válida.")

        if self.signature_type not in (0, 1, 2):
            raise RuntimeError("SIGNATURE_TYPE debe ser 0, 1 o 2 (normalmente 1).")

        if self.chain_id != 137:
            raise RuntimeError("CHAIN_ID esperado 137 (Polygon) para Polymarket.")

    def window_start_utc_iso(self) -> str:
        dt_utc = self.parse_local_dt(self.window_start_local).astimezone(pytz.UTC)
        return dt_utc.isoformat().replace("+00:00", "Z")

    def window_end_utc_iso(self) -> str:
        dt_utc = self.parse_local_dt(self.window_end_local).astimezone(pytz.UTC)
        return dt_utc.isoformat().replace("+00:00", "Z")


def load_config() -> Config:
    cfg = Config(
        gamma_host=_getenv("GAMMA_HOST", "https://gamma-api.polymarket.com"),
        clob_host=_getenv("CLOB_HOST", "https://clob.polymarket.com"),

        private_key=_getenv("PRIVATE_KEY", required=True),
        clob_api_key=_getenv("CLOB_API_KEY", required=True),
        clob_api_secret=_getenv("CLOB_API_SECRET", required=True),
        clob_api_passphrase=_getenv("CLOB_API_PASSPHRASE", required=True),

        chain_id=int(_getenv("CHAIN_ID", "137")),
        funder_address=_getenv("FUNDER_ADDRESS", required=True),
        signature_type=int(_getenv("SIGNATURE_TYPE", "1")),
        use_derived_creds=_getenv_bool("USE_DERIVED_CREDS", "true"),

        series_slug=_getenv("SERIES_SLUG", "btc-up-or-down-5m"),

        window_start_local=_getenv("WINDOW_START", required=True),
        window_end_local=_getenv("WINDOW_END", required=True),

        price_up=float(_getenv("PRICE_UP", required=True)),
        size_up=float(_getenv("SIZE_UP", required=True)),
        price_down=float(_getenv("PRICE_DOWN", required=True)),
        size_down=float(_getenv("SIZE_DOWN", required=True)),

        dry_run=_getenv_bool("DRY_RUN", "true"),
        max_markets=int(_getenv("MAX_MARKETS", "200")),

        polygon_rpc_url=_getenv("POLYGON_RPC_URL", "https://polygon-rpc.com"),
        auto_redeem=_getenv_bool("AUTO_REDEEM", "false"),
        auto_redeem_wait_seconds=int(_getenv("AUTO_REDEEM_WAIT_SECONDS", "20")),
    )

    cfg.validate()
    return cfg
