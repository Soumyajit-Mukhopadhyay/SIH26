"""TLS trust for the Indian government data estate.

The plan warns (from the blueprint's C.6) that the ``.gov.in`` hosts have flaky
TLS. The specific, diagnosed problem:

    erddap.incois.gov.in sends ONLY its leaf certificate and omits the
    GlobalSign RSA OV SSL CA 2018 intermediate.

``curl`` and browsers succeed anyway because they chase the certificate's
Authority Information Access extension to fetch the missing intermediate;
Python's OpenSSL does not, so httpx fails with
``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate``.

The wrong fix is ``verify=False``, which would silently accept any certificate
for INCOIS — on a safety-of-life-adjacent system, from a government source whose
authority is the entire point of citing it. The right fix, and what this module
does, is to **complete the chain**: trust certifi *plus* the specific
intermediates the servers fail to send, and keep verifying properly.

The intermediates live in ``backend/data/static/ca/`` as committed PEMs, so this
works offline and on a judge's laptop. :func:`refresh_intermediate` re-fetches
one via AIA when a certificate rotates.
"""

from __future__ import annotations

import logging
import ssl
from pathlib import Path

import certifi

from orca.config import BACKEND_ROOT

log = logging.getLogger(__name__)

CA_DIR = BACKEND_ROOT / "data" / "static" / "ca"
BUNDLE_PATH = BACKEND_ROOT / "data" / "ca-bundle.pem"

#: Hosts known to serve an incomplete chain, and the intermediate each needs.
#: Documented here rather than discovered at runtime so the failure mode is a
#: readable list instead of a mystery.
INCOMPLETE_CHAIN_HOSTS: dict[str, str] = {
    "erddap.incois.gov.in": "globalsign-rsa-ov-ssl-ca-2018.pem",
    "incois.gov.in": "globalsign-rsa-ov-ssl-ca-2018.pem",
}

#: Where to re-fetch each intermediate if it expires or rotates. These are the
#: AIA "CA Issuers" URIs read off the leaf certificates.
INTERMEDIATE_SOURCES: dict[str, str] = {
    "globalsign-rsa-ov-ssl-ca-2018.pem": (
        "http://secure.globalsign.com/cacert/gsrsaovsslca2018.crt"
    ),
}

_bundle: Path | None = None
_context: ssl.SSLContext | None = None


def extra_certificates() -> list[Path]:
    """Committed intermediates, if any are present."""
    if not CA_DIR.is_dir():
        return []
    return sorted(p for p in CA_DIR.glob("*.pem") if p.is_file())


def ca_bundle() -> Path:
    """Path to certifi's bundle concatenated with our extra intermediates.

    Rebuilt when an input is newer than the output, so dropping a new PEM into
    ``data/static/ca/`` is all it takes.
    """
    global _bundle
    extras = extra_certificates()
    if not extras:
        return Path(certifi.where())

    if _bundle is not None and _bundle.exists():
        return _bundle

    sources = [Path(certifi.where()), *extras]
    if BUNDLE_PATH.exists():
        newest_input = max(p.stat().st_mtime for p in sources)
        if BUNDLE_PATH.stat().st_mtime >= newest_input:
            _bundle = BUNDLE_PATH
            return _bundle

    BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with BUNDLE_PATH.open("wb") as out:
        for source in sources:
            out.write(source.read_bytes())
            out.write(b"\n")
    log.info(
        "built CA bundle with %d extra intermediate(s): %s",
        len(extras),
        ", ".join(p.stem for p in extras),
    )
    _bundle = BUNDLE_PATH
    return _bundle


def ssl_context() -> ssl.SSLContext:
    """A verifying context that also trusts our extra intermediates.

    Hostname checking and certificate verification stay **on**. The only thing
    this changes is that the chain can now be completed.
    """
    global _context
    if _context is None:
        _context = ssl.create_default_context(cafile=str(ca_bundle()))
    return _context


def reset() -> None:
    """Drop the cached bundle and context. For tests, and after a refresh."""
    global _bundle, _context
    _bundle = None
    _context = None


async def refresh_intermediate(filename: str) -> bool:
    """Re-fetch one intermediate from its AIA URI and rewrite the PEM.

    Handles both DER (what GlobalSign serves) and PEM, because AIA endpoints are
    inconsistent about which they return.
    """
    url = INTERMEDIATE_SOURCES.get(filename)
    if url is None:
        log.error("no AIA source recorded for %s", filename)
        return False

    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("could not fetch %s from %s: %s", filename, url, exc)
        return False

    raw = response.content
    try:
        if b"-----BEGIN CERTIFICATE-----" in raw:
            pem = raw
        else:
            der = ssl.DER_cert_to_PEM_cert(raw)
            pem = der.encode()
    except ValueError as exc:
        log.error("%s is neither DER nor PEM: %s", url, exc)
        return False

    CA_DIR.mkdir(parents=True, exist_ok=True)
    (CA_DIR / filename).write_bytes(pem)
    reset()
    log.info("refreshed intermediate %s from %s", filename, url)
    return True


def describe() -> dict[str, object]:
    """For ``/healthz``: what we trust beyond the system roots, and why."""
    extras = extra_certificates()
    return {
        "bundle": str(ca_bundle()),
        "extra_intermediates": [p.name for p in extras],
        "reason": (
            "These hosts serve an incomplete certificate chain (they omit their "
            "intermediate CA). We complete the chain rather than disable verification."
        ),
        "affected_hosts": sorted(INCOMPLETE_CHAIN_HOSTS),
    }
