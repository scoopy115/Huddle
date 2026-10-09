"""Client side of Huddle Server: the engine sends a prepared recording to a server the user runs
and fetches the result. Authentication is the client key from the server dashboard; transport is
TLS. A server without a public domain uses a self-signed certificate — its SHA-256 fingerprint is
shown in the dashboard, the user confirms it once here and the certificate is pinned.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from . import __version__
from .settings import EngineConfig

log = logging.getLogger(__name__)

PING_TIMEOUT = 8.0
UPLOAD_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=600.0, pool=15.0)
_transport_override: httpx.BaseTransport | None = None   # tests inject a MockTransport


class ServerError(Exception):
    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message)
        self.detail = detail


class UntrustedCertificate(ServerError):
    def __init__(self, fingerprint: str, subject: str, pem: str):
        super().__init__("The server uses a certificate this Mac does not know.")
        self.fingerprint, self.subject, self.pem = fingerprint, subject, pem


@dataclass
class ServerConfig:
    url: str
    api_key: str
    ca_path: Path | None = None       # pinned self-signed certificate

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or ""


def normalize_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u and not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u


def ca_file(cfg: EngineConfig, url: str) -> Path:
    slug = re.sub(r"[^a-z0-9.-]+", "_", (urlparse(normalize_url(url)).netloc or "server").lower())
    return cfg.data_dir / "servers" / f"{slug}.pem"


def config_from_settings(cfg: EngineConfig, settings: dict[str, Any]) -> ServerConfig | None:
    url = normalize_url(str(settings.get("server.url") or ""))
    key = str(settings.get("server.apiKey") or "").strip()
    if not url or not key:
        return None
    ca = ca_file(cfg, url)
    return ServerConfig(url=url, api_key=key, ca_path=ca if ca.exists() else None)


def _verify(sc: ServerConfig):
    if sc.ca_path and sc.ca_path.exists():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False            # a pinned self-signed cert has no meaningful SAN
        ctx.load_verify_locations(cafile=str(sc.ca_path))
        return ctx
    return True


def _client(sc: ServerConfig, timeout: Any) -> httpx.Client:
    headers = {"Authorization": f"Bearer {sc.api_key}", "User-Agent": f"huddle-engine/{__version__}",
               "X-Huddle-Client-Version": __version__}
    if _transport_override is not None:
        return httpx.Client(base_url=sc.url, headers=headers, timeout=timeout, transport=_transport_override)
    return httpx.Client(base_url=sc.url, headers=headers, timeout=timeout, verify=_verify(sc))


def _raise_for(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    try:
        detail = r.json().get("detail") or r.text
    except Exception:
        detail = r.text
    if r.status_code == 401:
        raise ServerError("The server rejected the client key. Generate a new key in the server dashboard.", detail)
    if r.status_code == 404:
        raise ServerError("The server no longer has this recording.", detail)
    raise ServerError(f"The server answered {r.status_code}.", str(detail)[:2000])


def _wrap(fn: Callable[[], Any], sc: ServerConfig) -> Any:
    try:
        return fn()
    except ServerError:
        raise
    except httpx.ConnectError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or "certificate" in str(e).lower():
            probe = probe_certificate(sc.url)
            raise UntrustedCertificate(probe["fingerprint"], probe["subject"], probe["pem"]) from e
        raise ServerError(f"Could not reach {sc.host}.", str(e)) from e
    except httpx.TimeoutException as e:
        raise ServerError(f"{sc.host} did not answer in time.", str(e)) from e
    except httpx.HTTPError as e:
        raise ServerError(f"Talking to {sc.host} failed.", str(e)) from e


def probe_certificate(url: str) -> dict[str, str]:
    """Fetch the server's certificate without verifying it: fingerprint for the user to compare
    with the dashboard, PEM to pin after confirmation."""
    u = urlparse(normalize_url(url))
    host, port = u.hostname or "", u.port or 443
    try:
        pem = ssl.get_server_certificate((host, port), timeout=PING_TIMEOUT)
    except (TimeoutError, OSError) as e:
        raise ServerError(f"Could not reach {host}.", str(e)) from e
    der = ssl.PEM_cert_to_DER_cert(pem)
    fp = hashlib.sha256(der).hexdigest()
    subject = ""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=PING_TIMEOUT) as sock, ctx.wrap_socket(sock, server_hostname=host) as ss:
            info = ss.getpeercert(binary_form=False)
            # with CERT_NONE the parsed form is empty; keep the common name from the PEM instead
            subject = str(dict(x[0] for x in (info or {}).get("subject", ())).get("commonName", "")) if info else ""
    except Exception:
        pass
    return {"fingerprint": format_fingerprint(fp), "subject": subject or host, "pem": pem}


def format_fingerprint(hex_digest: str) -> str:
    h = re.sub(r"[^0-9a-f]", "", hex_digest.lower())
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2)).upper()


def trust(cfg: EngineConfig, url: str, expected_fingerprint: str) -> str:
    """Pin the certificate `url` presents right now, provided it matches what the user confirmed."""
    probe = probe_certificate(url)
    if format_fingerprint(expected_fingerprint) != probe["fingerprint"]:
        raise ServerError("The certificate changed between checking and trusting it. Try again.")
    path = ca_file(cfg, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(probe["pem"], encoding="utf-8")
    return probe["fingerprint"]


# ---- API ------------------------------------------------------------------- #
def ping(sc: ServerConfig) -> dict[str, Any]:
    def go():
        with _client(sc, PING_TIMEOUT) as c:
            r = c.get("/v1/ping")
            _raise_for(r)
            return r.json()
    return _wrap(go, sc)


class _ProgressFile:
    """File object whose reads report how far the upload got."""
    def __init__(self, path: Path, on_progress: Callable[[float], None] | None):
        self._f = path.open("rb")
        self._total = max(1, path.stat().st_size)
        self._sent = 0
        self._cb = on_progress

    def read(self, n: int = -1) -> bytes:
        chunk = self._f.read(n)
        self._sent += len(chunk)
        if self._cb:
            self._cb(min(1.0, self._sent / self._total))
        return chunk

    def __getattr__(self, name):
        return getattr(self._f, name)


def upload(sc: ServerConfig, recording_id: str, audio: Path, meta: dict[str, Any],
           on_progress: Callable[[float], None] | None = None) -> dict[str, Any]:
    def go():
        pf = _ProgressFile(audio, on_progress)
        try:
            with _client(sc, UPLOAD_TIMEOUT) as c:
                r = c.post("/v1/recordings", data={"meta": json.dumps(meta)},
                           files={"audio": (audio.name, pf, "audio/flac" if audio.suffix == ".flac" else "audio/wav")})
                _raise_for(r)
                return r.json()
        finally:
            pf.close()
    return _wrap(go, sc)


def status(sc: ServerConfig, recording_id: str) -> dict[str, Any]:
    def go():
        with _client(sc, PING_TIMEOUT) as c:
            r = c.get(f"/v1/recordings/{recording_id}")
            _raise_for(r)
            return r.json()
    return _wrap(go, sc)


def result(sc: ServerConfig, recording_id: str) -> dict[str, Any]:
    def go():
        with _client(sc, httpx.Timeout(60.0)) as c:
            r = c.get(f"/v1/recordings/{recording_id}/result")
            _raise_for(r)
            return r.json()
    return _wrap(go, sc)


def retry(sc: ServerConfig, recording_id: str) -> dict[str, Any]:
    def go():
        with _client(sc, PING_TIMEOUT) as c:
            r = c.post(f"/v1/recordings/{recording_id}/process")
            _raise_for(r)
            return r.json()
    return _wrap(go, sc)


def cancel(sc: ServerConfig, recording_id: str) -> None:
    try:
        with _client(sc, PING_TIMEOUT) as c:
            c.post(f"/v1/recordings/{recording_id}/cancel")
    except Exception as e:   # best effort
        log.info("remote cancel failed: %s", e)
