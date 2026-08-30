"""Logging, with a hard guarantee that no credential reaches a log line.

``SecretStr`` stops *accidental* interpolation, but it cannot stop a library from
logging a URL it was handed, or an exception message that quotes a request. So
there is a second, independent line of defence: :class:`SecretScrubber` is
installed on the root handler and rewrites any known secret value — and anything
matching a credential-shaped pattern — before the record is formatted.

Two layers, because one of them will eventually be bypassed by a dependency we
do not control.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any, ClassVar

from orca.config import Settings, mask_dsn

#: Credential-shaped strings, scrubbed even if we have never seen the value.
#: Catches keys pasted into a request by a library, or a leaked upstream token.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:gsk|sk|pk|hf|sb)_[A-Za-z0-9_\-]{12,}"),  # groq/openai/hf/supabase
    re.compile(r"\bsk-(?:or-v1-|lf-|proj-)?[A-Za-z0-9_\-]{16,}"),  # openrouter / langfuse
    re.compile(r"\bAQ\.[A-Za-z0-9_\-]{16,}"),  # new-format Google keys
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"(?i)\b(api[-_]?key|token|password|secret)\b\s*[=:]\s*['\"]?([^\s'\",;&]{8,})"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|redis|rediss|mysql|mongodb(?:\+srv)?)://[^\s'\"]+"),
)

_REDACTED = "[REDACTED]"


class SecretScrubber(logging.Filter):
    """Rewrites secrets out of every record that passes through a handler.

    Mutates ``record.msg``/``record.args`` rather than only the formatted string,
    so a handler that formats differently (JSON, syslog) is still covered.
    """

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        # Longest first: scrubbing a DSN before its embedded password means the
        # password's own pass finds nothing left to do, and we avoid a partially
        # masked string that still leaks the tail.
        self._secrets = sorted({s for s in (secrets or []) if len(s) >= 8}, key=len, reverse=True)

    def add_secret(self, value: str) -> None:
        if len(value) >= 8 and value not in self._secrets:
            self._secrets.append(value)
            self._secrets.sort(key=len, reverse=True)

    def scrub(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, _REDACTED)
        for pattern in _PATTERNS:
            text = pattern.sub(_replace_match, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.scrub(record.msg)
        if record.args:
            record.args = _scrub_args(record.args, self.scrub)
        if record.exc_text:
            record.exc_text = self.scrub(record.exc_text)
        return True


def _replace_match(m: re.Match[str]) -> str:
    text = m.group(0)
    # DSNs keep their host — 'cannot reach aws-0-ap-southeast-1...' is the whole
    # diagnostic value of the message, and mask_dsn already drops the password.
    if "://" in text:
        return mask_dsn(text)
    # `key=value` forms: keep the key name so the log still says *which* one.
    if m.lastindex == 2 and m.group(1):
        return f"{m.group(1)}={_REDACTED}"
    return _REDACTED


def _scrub_args(args: Any, scrub: Any) -> Any:
    if isinstance(args, dict):
        return {k: scrub(v) if isinstance(v, str) else v for k, v in args.items()}
    if isinstance(args, tuple):
        return tuple(scrub(a) if isinstance(a, str) else a for a in args)
    return args


class _Formatter(logging.Formatter):
    """Compact, aligned, and greppable. Colour only when attached to a TTY."""

    _COLOURS: ClassVar[dict[str, str]] = {
        "DEBUG": "\x1b[38;5;245m",
        "INFO": "\x1b[38;5;39m",
        "WARNING": "\x1b[38;5;214m",
        "ERROR": "\x1b[38;5;203m",
        "CRITICAL": "\x1b[48;5;203;38;5;231m",
    }
    _RESET = "\x1b[0m"

    def __init__(self, *, colour: bool) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)-28s %(message)s",
            datefmt="%H:%M:%S",
        )
        self.colour = colour

    def format(self, record: logging.LogRecord) -> str:
        if not self.colour:
            return super().format(record)
        colour = self._COLOURS.get(record.levelname, "")
        original = record.levelname
        record.levelname = f"{colour}{original}{self._RESET}"
        try:
            # levelname is padded to 8 before colouring inflates its length, so
            # pad the plain name and let the escape codes ride along.
            record.levelname = f"{colour}{original:<8}{self._RESET}"
            return super().format(record).replace(f"{self._RESET} ", f"{self._RESET}", 1)
        finally:
            record.levelname = original


_scrubber: SecretScrubber | None = None


def configure_logging(settings: Settings) -> SecretScrubber:
    """Install the root handler and the scrubber. Idempotent."""
    global _scrubber

    scrubber = SecretScrubber(settings.secret_values())
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter(colour=sys.stderr.isatty()))
    handler.addFilter(scrubber)

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.orca_log_level)

    # These are chatty at INFO and tell us nothing we do not already log.
    for noisy in (
        "httpx",
        "httpcore",
        "asyncio",
        "urllib3",
        "matplotlib",
        "PIL",
        "apscheduler.executors.default",
        "botocore",
        "watchfiles",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _scrubber = scrubber
    return scrubber


def get_scrubber() -> SecretScrubber:
    """The installed scrubber, for code that needs to sanitise a string it is
    about to put somewhere other than a log — an SSE frame, or a stored trace."""
    global _scrubber
    if _scrubber is None:
        _scrubber = SecretScrubber([])
    return _scrubber


def scrub(text: str) -> str:
    """Sanitise arbitrary text with the installed scrubber."""
    return get_scrubber().scrub(text)
