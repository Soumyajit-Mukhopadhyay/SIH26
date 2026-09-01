"""Every environment variable ORCA reads passes through this module.

Two rules the rest of the codebase depends on:

1. **No module calls ``os.environ`` directly.** If it is configurable, it is a
   field on :class:`Settings`. That is what makes "which credentials does this
   thing actually need?" a question with an answer.
2. **Secrets are ``SecretStr``.** They cannot be interpolated into a log line or
   an error message by accident, and :func:`Settings.redacted` produces the
   masked view used by ``/healthz`` and startup logging.

Capability flags (``has_groq``, ``has_imd``, …) exist so an adapter can be
*written* against a credential that has not arrived yet and report itself as
dormant rather than crashing at import.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

#: Repo root — this file is backend/orca/config.py, so up three levels.
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"


class DbDriver(StrEnum):
    AUTO = "auto"
    """Try PostGIS; on any failure fall back to SQLite and record why."""
    SQLITE = "sqlite"
    POSTGIS = "postgis"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- runtime
    orca_env: Literal["dev", "demo", "prod", "test"] = "dev"
    orca_log_level: str = "INFO"
    orca_api_host: str = "127.0.0.1"
    orca_api_port: int = 8000
    orca_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ------------------------------------------------------------ persistence
    orca_db_driver: DbDriver = DbDriver.AUTO
    orca_sqlite_path: Path = Path("backend/data/orca.db")
    database_url: SecretStr | None = None
    #: Not a SecretStr on purpose: this is the docker-compose DSN whose
    #: credentials are published in docker-compose.yml and .env.example. Typing a
    #: deliberately-public dev default as a secret would blank it out of every
    #: connection-error log line and make local debugging worse for no gain.
    database_url_local: str | None = None

    # ------------------------------------------------------ cache / job queue
    redis_url: SecretStr | None = None

    # --------------------------------------------------------- LLM providers
    groq_api_key_primary: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"

    # -------------------------------------------------------- language plane
    sarvam_api_key: SecretStr | None = None
    hf_token: SecretStr | None = None
    bhashini_user_id: SecretStr | None = None
    bhashini_ulca_api_key: SecretStr | None = None

    # ------------------------------------------------------ ocean / satellite
    cmems_username: str | None = None
    cmems_password: SecretStr | None = None
    earthdata_username: str | None = None
    earthdata_password: SecretStr | None = None
    cdse_client_id: str | None = None
    cdse_client_secret: SecretStr | None = None
    mosdac_username: str | None = None
    mosdac_password: SecretStr | None = None
    imd_api_key: SecretStr | None = None
    data_gov_in_api_key: SecretStr | None = None
    worldtides_api_key: SecretStr | None = None

    # --------------------------------------------------------------- vessels
    aisstream_api_key: SecretStr | None = None
    gfw_api_token: SecretStr | None = None

    # -------------------------------------------------------------- frontend
    cesium_ion_token: SecretStr | None = None

    # ------------------------------------------------------------------ auth
    supabase_url: str | None = None
    supabase_publishable_key: SecretStr | None = None
    supabase_secret_key: SecretStr | None = None
    supabase_jwks_url: str | None = None

    # --------------------------------------------------------- observability
    langfuse_host: str = "https://us.cloud.langfuse.com"
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None

    # ------------------------------------------------------ tuning / policy
    http_timeout_s: float = 12.0
    http_retries: int = 3
    circuit_breaker_failures: int = 4
    circuit_breaker_reset_s: float = 120.0
    llm_requests_per_session_per_hour: int = 40
    openrouter_daily_budget: int = 45  # free tier is 50/day; leave headroom

    # ------------------------------------------------------------ validators
    @field_validator("orca_log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = v.upper()
        if v not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}:
            raise ValueError(f"invalid log level {v!r}")
        return v

    @field_validator(
        "database_url",
        "redis_url",
        "groq_api_key_primary",
        "groq_api_key",
        "google_api_key",
        "openrouter_api_key",
        "sarvam_api_key",
        "hf_token",
        "bhashini_user_id",
        "bhashini_ulca_api_key",
        "cmems_password",
        "earthdata_password",
        "cdse_client_secret",
        "mosdac_password",
        "imd_api_key",
        "data_gov_in_api_key",
        "worldtides_api_key",
        "aisstream_api_key",
        "gfw_api_token",
        "cesium_ion_token",
        "supabase_publishable_key",
        "supabase_secret_key",
        "langfuse_public_key",
        "langfuse_secret_key",
        mode="before",
    )
    @classmethod
    def _blank_secret_is_absent(cls, v: Any) -> Any:
        """``IMD_API_KEY=`` in .env means "not supplied yet", not "the key is the
        empty string". Without this, every dormant adapter would think it had a
        credential."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator(
        "database_url_local",
        "cmems_username",
        "earthdata_username",
        "cdse_client_id",
        "mosdac_username",
        "supabase_url",
        "supabase_jwks_url",
        mode="before",
    )
    @classmethod
    def _blank_plain_is_absent(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # ------------------------------------------------------- derived values
    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.orca_cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlite_file(self) -> Path:
        p = self.orca_sqlite_path
        return p if p.is_absolute() else (REPO_ROOT / p)

    @property
    def data_dir(self) -> Path:
        return BACKEND_ROOT / "data"

    @property
    def raster_dir(self) -> Path:
        return self.data_dir / "rasters"

    @property
    def static_dir(self) -> Path:
        return self.data_dir / "static"

    @property
    def checkpoint_file(self) -> Path:
        return self.data_dir / "checkpoints" / "agent.sqlite"

    @property
    def is_demo(self) -> bool:
        return self.orca_env == "demo"

    # ----------------------------------------------------- capability flags
    # Written as properties rather than sprinkled `if settings.x_key` checks so
    # /healthz can enumerate them, and so an adapter's dormancy is a fact about
    # configuration rather than a caught exception.

    @property
    def has_groq(self) -> bool:
        return self.groq_api_key_primary is not None or self.groq_api_key is not None

    @property
    def has_gemini(self) -> bool:
        return self.google_api_key is not None

    @property
    def has_openrouter(self) -> bool:
        return self.openrouter_api_key is not None

    @property
    def has_sarvam(self) -> bool:
        return self.sarvam_api_key is not None

    @property
    def has_bhashini(self) -> bool:
        return self.bhashini_user_id is not None and self.bhashini_ulca_api_key is not None

    @property
    def has_cmems(self) -> bool:
        return self.cmems_username is not None and self.cmems_password is not None

    @property
    def has_earthdata(self) -> bool:
        return self.earthdata_username is not None and self.earthdata_password is not None

    @property
    def has_cdse(self) -> bool:
        return self.cdse_client_id is not None and self.cdse_client_secret is not None

    @property
    def has_mosdac(self) -> bool:
        return self.mosdac_username is not None and self.mosdac_password is not None

    @property
    def has_imd(self) -> bool:
        return self.imd_api_key is not None

    @property
    def has_worldtides(self) -> bool:
        return self.worldtides_api_key is not None

    @property
    def has_ais(self) -> bool:
        return self.aisstream_api_key is not None

    @property
    def has_gfw(self) -> bool:
        return self.gfw_api_token is not None

    @property
    def has_langfuse(self) -> bool:
        return self.langfuse_public_key is not None and self.langfuse_secret_key is not None

    @property
    def has_supabase_auth(self) -> bool:
        return self.supabase_url is not None and self.supabase_secret_key is not None

    @property
    def has_postgis(self) -> bool:
        return (
            self.orca_db_driver is not DbDriver.SQLITE and self.effective_database_url is not None
        )

    @property
    def has_redis(self) -> bool:
        return self.redis_url is not None

    @property
    def effective_database_url(self) -> str | None:
        """``DATABASE_URL`` if set, else the local docker-compose DSN.

        Returns ``None`` under an explicit ``sqlite`` driver so that setting the
        driver actually settles the question — a stray DSN in .env must not
        quietly re-enable Postgres.
        """
        if self.orca_db_driver is DbDriver.SQLITE:
            return None
        if self.database_url is not None:
            return self.database_url.get_secret_value()
        return self.database_url_local

    # ------------------------------------------------------------- redaction
    def redacted(self) -> dict[str, Any]:
        """The only sanctioned way to render settings for a human.

        Secrets become ``gsk_…LXtK``-style fingerprints: enough to tell two keys
        apart when debugging, not enough to use.
        """
        out: dict[str, Any] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                out[name] = _fingerprint(value.get_secret_value(), url_aware="url" in name)
            elif isinstance(value, Path):
                out[name] = str(value)
            elif isinstance(value, StrEnum):
                out[name] = value.value
            else:
                out[name] = value
        return out

    def capabilities(self) -> dict[str, bool]:
        """Every ``has_*`` flag, for ``/healthz`` and the startup banner."""
        return {
            name[4:]: bool(getattr(self, name))
            for name in dir(type(self))
            if name.startswith("has_") and isinstance(getattr(type(self), name), property)
        }

    def secret_values(self) -> list[str]:
        """Raw secret strings, for the logging filter to scrub. Never logged,
        never returned over HTTP — the log filter is its only consumer."""
        values: list[str] = []
        for name in type(self).model_fields:
            v = getattr(self, name)
            if isinstance(v, SecretStr):
                raw = v.get_secret_value()
                if len(raw) < _MIN_SCRUBBABLE:
                    continue
                # A DSN with no password holds no secret — `redis://127.0.0.1:6380/0`
                # is pure diagnostics. Blanking it would turn a useful log line into
                # "Redis reachable at [REDACTED]", which is strictly worse than the
                # truth and protects nothing. DSNs *with* a password still go in.
                if "://" in raw and _dsn_password(raw) is None:
                    continue
                values.append(raw)
                # A DSN's password is the part that must not leak, and it can
                # appear on its own in a driver's error message, so scrub it
                # separately from the whole DSN.
                pw = _dsn_password(raw)
                if pw and len(pw) >= _MIN_SCRUBBABLE:
                    values.append(pw)
        return values


#: Shorter strings are not scrubbed by exact match. The local docker-compose
#: password is the literal ``orca`` — treating that as a secret would blank the
#: word "orca" out of every log line in the project, which is worse than useless.
#: Short passwords are still protected inside a DSN by :func:`mask_dsn`.
_MIN_SCRUBBABLE = 8


#: Below this length, a secret is almost certainly a human password rather than a
#: provider-issued key, and the prefix/suffix trick that usefully identifies an
#: API key would instead disclose most of the password. Long keys reveal
#: ``gsk_Gp...LXtK``; short ones reveal nothing but their length.
_FINGERPRINT_MIN_LENGTH = 24


def _fingerprint(raw: str, *, url_aware: bool = False) -> str:
    """``gsk_Gp...LXtK (len 56)`` — identifies a key without disclosing it.

    A fingerprint has exactly one job: let a human tell two credentials apart
    while debugging. For a 56-character API key, six leading and four trailing
    characters do that and reveal nothing usable. For a 14-character password
    they would reveal ten of its characters, so short secrets are masked whole.
    """
    if not raw:
        return ""
    if url_aware:
        masked = mask_dsn(raw)
        if masked != raw:
            return masked
    if len(raw) < _FINGERPRINT_MIN_LENGTH:
        return f"*** (len {len(raw)})"
    return f"{raw[:6]}...{raw[-4:]} (len {len(raw)})"


def _dsn_password(dsn: str) -> str | None:
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return None
    return parts.password


def mask_dsn(dsn: str) -> str:
    """``postgresql://user:***@host:5432/db`` — safe to log, still diagnosable."""
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return "***"
    if not parts.hostname:
        return "***"
    userinfo = ""
    if parts.username:
        userinfo = quote(parts.username, safe="")
        if parts.password:
            userinfo += ":***"
        userinfo += "@"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{userinfo}{parts.hostname}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached, so .env is read exactly once."""
    return Settings()


def reload_settings() -> Settings:
    """Drop the cache. For tests that monkeypatch the environment."""
    get_settings.cache_clear()
    return get_settings()
