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

    # Local time (Europe/Madrid)
    window_start_local: str
    window_end_local: str

    # Orders
    price_up: float
    size_up: float
    price_down: float
    size_down: float

    dry_run: bool
    max_markets: int

    @property
    def tz(self):
        return pytz.timezone("Europe/Madrid")

    def parse_local_dt(self, s: str) -> datetime:
        # Input: "YYYY-MM-DDTHH:MM:SS" interpreted in Europe/Madrid
        naive = datetime.fromisoformat(s)
        return self.tz.localize(naive)

    def window_start_utc_iso(self) -> str:
        dt_utc = self.parse_local_dt(self.window_start_local).astimezone(pytz.UTC)
        return dt_utc.isoformat().replace("+00:00", "Z")

    def window_end_utc_iso(self) -> str:
        dt_utc = self.parse_local_dt(self.window_end_local).astimezone(pytz.UTC)
        return dt_utc.isoformat().replace("+00:00", "Z")

    def validate(self) -> None:
        # Ensure end > start in local time (prevents Gamma 422)
        if self.parse_local_dt(self.window_end_local) <= self.parse_local_dt(self.window_start_local):
            raise RuntimeError("WINDOW_END debe ser posterior a WINDOW_START (hora local Europe/Madrid).")


def _getenv(name: str, default: str | None = None, required: bool = False) -> str:
    v = os.getenv(name, default)
    if required and (v is None or str(v).strip() == ""):
        raise RuntimeError(f"Falta variable de entorno requerida: {name}")
    return v.strip() if isinstance(v, str) else str(v)


def load_config() -> Config:
    gamma_host = _getenv("GAMMA_HOST", "https://gamma-api.polymarket.com")
    clob_host = _getenv("CLOB_HOST", "https://clob.polymarket.com")

    funder_address = _getenv("FUNDER_ADDRESS", required=True)
    signature_type = int(_getenv("SIGNATURE_TYPE", "0"))

    private_key = _getenv("PRIVATE_KEY", required=True)
    clob_api_key = _getenv("CLOB_API_KEY", required=True)
    clob_api_secret = _getenv("CLOB_API_SECRET", required=True)
    clob_api_passphrase = _getenv("CLOB_API_PASSPHRASE", required=True)
    chain_id = int(_getenv("CHAIN_ID", "137"))

    series_slug = _getenv("SERIES_SLUG", "btc-up-or-down-5m")

    window_start = _getenv("WINDOW_START", required=True)
    window_end = _getenv("WINDOW_END", required=True)

    price_up = float(_getenv("PRICE_UP", required=True))
    size_up = float(_getenv("SIZE_UP", required=True))
    price_down = float(_getenv("PRICE_DOWN", required=True))
    size_down = float(_getenv("SIZE_DOWN", required=True))

    dry_run = _getenv("DRY_RUN", "true").strip().lower() in ("1", "true", "yes", "y")
    max_markets = int(_getenv("MAX_MARKETS", "200"))

    cfg = Config(
        gamma_host=gamma_host,
        clob_host=clob_host,
        funder_address=funder_address,
        signature_type=signature_type,
        private_key=private_key,
        clob_api_key=clob_api_key,
        clob_api_secret=clob_api_secret,
        clob_api_passphrase=clob_api_passphrase,
        chain_id=chain_id,
        series_slug=series_slug,
        window_start_local=window_start,
        window_end_local=window_end,
        price_up=price_up,
        size_up=size_up,
        price_down=price_down,
        size_down=size_down,
        dry_run=dry_run,
        max_markets=max_markets,
    )

    cfg.validate()
    return cfg
