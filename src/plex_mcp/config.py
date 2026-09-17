"""Configuration, loaded from environment variables and an optional .env file."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _env(*names: str) -> AliasChoices:
    return AliasChoices(*names)


class Settings(BaseSettings):
    """All settings for the server. Every field maps to an environment variable."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",  # tolerate a BOM from Notepad
        extra="ignore",
        populate_by_name=True,
        # Each field reads only its explicit alias below. This unused prefix stops field names like
        # `path`, `host`, and `port` from silently picking up the PATH/HOST/PORT environment variables.
        env_prefix="PLEX_MCP_FIELD_",
    )

    # --- Plex connection -------------------------------------------------
    plex_url: str = Field(
        default="http://127.0.0.1:32400",
        validation_alias=_env("PLEX_URL"),
        description="Base URL of Plex Media Server. On the Plex host itself, http://127.0.0.1:32400 works.",
    )
    plex_token: SecretStr = Field(
        validation_alias=_env("PLEX_TOKEN"),
        description="X-Plex-Token for an admin account on the server.",
    )
    plex_timeout: int = Field(default=30, validation_alias=_env("PLEX_TIMEOUT"))
    plex_verify_ssl: bool = Field(default=True, validation_alias=_env("PLEX_VERIFY_SSL"))

    # --- HTTP transport --------------------------------------------------
    host: str = Field(default="0.0.0.0", validation_alias=_env("PLEX_MCP_HOST"))
    port: int = Field(default=8765, validation_alias=_env("PLEX_MCP_PORT"))
    path: str = Field(default="/mcp", validation_alias=_env("PLEX_MCP_PATH"))
    auth_token: SecretStr | None = Field(
        default=None,
        validation_alias=_env("PLEX_MCP_AUTH_TOKEN"),
        description="Bearer token that HTTP clients must send. Required unless binding to localhost.",
    )
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=_env("PLEX_MCP_ALLOWED_HOSTS"),
        description=(
            "Comma-separated Host header values accepted over HTTP (DNS-rebinding protection). "
            "Empty = auto-detect this machine's hostname and LAN IPs. '*' disables the check."
        ),
    )

    # --- Safety ----------------------------------------------------------
    read_only: bool = Field(
        default=False,
        validation_alias=_env("PLEX_MCP_READ_ONLY"),
        description="When true, no tool that changes anything is registered.",
    )
    allow_delete: bool = Field(
        default=True,
        validation_alias=_env("PLEX_MCP_ALLOW_DELETE"),
        description="When false, delete tools (collections, playlists) are not registered.",
    )
    allowed_libraries: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=_env("PLEX_MCP_ALLOWED_LIBRARIES"),
        description="Comma-separated library titles or IDs the server may touch. Empty = all libraries.",
    )
    max_results: int = Field(default=200, validation_alias=_env("PLEX_MCP_MAX_RESULTS"))

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", validation_alias=_env("PLEX_MCP_LOG_LEVEL")
    )
    log_file: str | None = Field(
        default=None,
        validation_alias=_env("PLEX_MCP_LOG_FILE"),
        description="Also write logs to this file (rotated at 5 MB). Useful when running as a background task.",
    )

    @field_validator("allowed_hosts", "allowed_libraries", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("plex_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


def load_settings(env_file: str | None = None) -> Settings:
    """Load settings, optionally from a specific .env file."""
    if env_file:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    return Settings()  # type: ignore[call-arg]
