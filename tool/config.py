import os
from dataclasses import dataclass
from datetime import datetime
import pytz


@dataclass
class Config:
    gamma_host: str
    clob_host: str

    # Trading identity
    funder_address: str          # address que PAGA el colateral en CLOB (tu wallet)
    signature_type: int          # 0 = EOA, 1 = proxy (depende de tu setup)
    chain_id: int                # 137 Polygon

    # Auth
    private_key: str
    use_derived_creds: bool
    clob_api_key: str | None
    clob_api_secret: str | None
    clob_api_passphrase: str | None

    # Series
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
        naive = datetime.fromisoformat(s)
        return self.tz.localize(naive)

    def window_start_utc_iso(self) -> str:
        dt_utc = self.parse_local_dt(self.window_start_local).astimezone(pytz.UTC)
        return dt_utc.isoformat().replace("+00:00", "Z")

    def window_end_utc_iso(self) -> str:
        dt_utc = self.parse_local_dt(self.window_end_local).astimezone(pytz.UTC)
        return dt_utc.isoformat().replace("+00:00", "Z")


def _getenv(name: str, default: str | None = None, required: bool = False) -> str | None:
    v = os.getenv(name, default)
    if v is None:
        if required:
            raise RuntimeError(f"Falta variable de entorno requerida: {name}")
        return None
    if isinstance(v, str):
        v = v.strip()
        if required and v == "":
            raise RuntimeError(f"Falta variable de entorno requerida: {name}")
        return v
    return v


def _getenv_bool(name: str, default: str = "false") -> bool:
    v = (_getenv(name, default) or default).strip().lower()
    return v in ("1", "true", "yes", "y")


def load_config() -> Config:
    gamma_host = _getenv("GAMMA_HOST", "https://gamma-api.polymarket.com")  # Gamma
    clob_host = _getenv("CLOB_HOST", "https://clob.polymarket.com")         # CLOB

    funder_address = _getenv("FUNDER_ADDRESS", required=True)
    signature_type = int(_getenv("SIGNATURE_TYPE", "1") or "1")  # recomendado 1 en tu caso
    chain_id = int(_getenv("CHAIN_ID", "137") or "137")

    private_key = _getenv("PRIVATE_KEY", required=True)

    # Creds: o derivadas (default) o las pasas tú
    use_derived_creds = _getenv_bool("USE_DERIVED_CREDS", "true")
    clob_api_key = _getenv("CLOB_API_KEY", None, required=False)
    clob_api_secret = _getenv("CLOB_API_SECRET", None, required=False)
    clob_api_passphrase = _getenv("CLOB_API_PASSPHRASE", None, required=False)

    series_slug = _getenv("SERIES_SLUG", "btc-up-or-down-5m")

    window_start = _getenv("WINDOW_START", required=True)
    window_end = _getenv("WINDOW_END", required=True)

    price_up = float(_getenv("PRICE_UP", required=True) or "0")
    size_up = float(_getenv("SIZE_UP", required=True) or "0")
    price_down = float(_getenv("PRICE_DOWN", required=True) or "0")
    size_down = float(_getenv("SIZE_DOWN", required=True) or "0")

    dry_run = _getenv_bool("DRY_RUN", "true")
    max_markets = int(_getenv("MAX_MARKETS", "200") or "200")

    cfg = Config(
        gamma_host=gamma_host or "",
        clob_host=clob_host or "",
        funder_address=funder_address or "",
        signature_type=signature_type,
        chain_id=chain_id,
        private_key=private_key or "",
        use_derived_creds=use_derived_creds,
        clob_api_key=clob_api_key,
        clob_api_secret=clob_api_secret,
        clob_api_passphrase=clob_api_passphrase,
        series_slug=series_slug or "",
        window_start_local=window_start or "",
        window_end_local=window_end or "",
        price_up=price_up,
        size_up=size_up,
        price_down=price_down,
        size_down=size_down,
        dry_run=dry_run,
        max_markets=max_markets,
    )

    # Validación ventana
    if cfg.parse_local_dt(cfg.window_end_local) <= cfg.parse_local_dt(cfg.window_start_local):
        raise RuntimeError("WINDOW_END debe ser posterior a WINDOW_START (en hora local Europe/Madrid).")

    # Validación creds si NO derivadas
    if not cfg.use_derived_creds:
        missing = []
        if not cfg.clob_api_key: missing.append("CLOB_API_KEY")
        if not cfg.clob_api_secret: missing.append("CLOB_API_SECRET")
        if not cfg.clob_api_passphrase: missing.append("CLOB_API_PASSPHRASE")
        if missing:
            raise RuntimeError(
                "USE_DERIVED_CREDS=false pero faltan variables: " + ", ".join(missing)
            )

    return cfg
