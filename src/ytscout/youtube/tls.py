"""TLS trust for every Google call: certifi plus the Windows root store.

Avast Web/Mail Shield on this PC intercepts HTTPS and re-signs it with its own root, which
lives in the Windows certificate store but not in the ``certifi`` bundle that both TLS
stacks in play default to: ``httplib2`` (the Data API and Analytics API clients) and
``requests`` (google-auth's token refresh and the consent flow). Neither stack can be told
to use the Windows store directly; ``httplib2`` builds its own ``SSLContext`` from a PEM
file path, so the ``truststore`` package would not reach it.

So this module writes one combined PEM bundle, ``<data_dir>/ca-bundle.pem`` (gitignored),
holding certifi's roots plus every server-authentication root in the Windows ``ROOT``
store, and every client is handed that path: ``httplib2.Http(ca_certs=...)`` and
``requests.Session.verify``. An existing ``HTTPLIB2_CA_CERTS`` (or, failing that,
``REQUESTS_CA_BUNDLE``) wins over the combined bundle so a hand-made bundle keeps working.
TLS verification is never turned off.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_HTTPLIB2 = "HTTPLIB2_CA_CERTS"
ENV_REQUESTS = "REQUESTS_CA_BUNDLE"
ENV_ORDER: tuple[str, ...] = (ENV_HTTPLIB2, ENV_REQUESTS)
BUNDLE_FILENAME = "ca-bundle.pem"
SOURCE_COMBINED = "certifi + Windows root store"
SOURCE_CERTIFI = "certifi"

_ROOT_STORE = "ROOT"
_X509 = "x509_asn"
_SERVER_AUTH_OID = "1.3.6.1.5.5.7.3.1"  # id-kp-serverAuth
_BEGIN = "-----BEGIN CERTIFICATE-----"
_WINDOWS_HEADER = "\n# --- Windows ROOT store (added by ytscout.youtube.tls) ---\n"


class TlsError(Exception):
    """A CA bundle named by the environment is unusable."""


@dataclass(frozen=True)
class CaBundle:
    """Which PEM file verifies Google's certificates, and where it came from."""

    path: Path
    source: str  # an ENV_* name, SOURCE_COMBINED or SOURCE_CERTIFI
    certificates: int  # certificates in the file
    windows_roots: int  # of which came from the Windows store (0 unless SOURCE_COMBINED)

    @property
    def from_environment(self) -> bool:
        return self.source in ENV_ORDER

    def describe(self) -> str:
        """One line for ``ytscout doctor``; a path and counts, nothing secret."""
        if self.from_environment:
            return f"{self.path} ({self.certificates} certificates, from {self.source})"
        if self.source == SOURCE_COMBINED:
            certifi_roots = self.certificates - self.windows_roots
            return (
                f"{self.path} (certifi {certifi_roots} + Windows root store {self.windows_roots})"
            )
        return f"{self.path} ({self.certificates} certificates, {self.source})"


def certifi_pem() -> str:
    """The text of certifi's bundle."""
    import certifi

    return Path(certifi.where()).read_text(encoding="utf-8")


def windows_root_pems() -> list[str]:
    """PEM text of each root in the Windows ``ROOT`` store trusted for server authentication.

    Empty on a platform without ``ssl.enum_certificates``. Entries are de-duplicated and
    kept in store order.
    """
    enumerate_store = getattr(ssl, "enum_certificates", None)
    if enumerate_store is None:
        return []
    pems: list[str] = []
    seen: set[bytes] = set()
    for der, encoding, trust in enumerate_store(_ROOT_STORE):
        if encoding != _X509 or der in seen:
            continue
        # ``trust`` is True (all purposes) or the set of EKU OIDs the store trusts it for.
        if trust is not True and _SERVER_AUTH_OID not in trust:
            continue
        seen.add(der)
        pems.append(ssl.DER_cert_to_PEM_cert(der))
    return pems


def count_certificates(pem: str) -> int:
    return pem.count(_BEGIN)


def combine(certifi_text: str, windows_pems: Iterable[str]) -> str:
    """certifi's bundle followed by the Windows roots. Pure; no store or file access."""
    extra = [pem if pem.endswith("\n") else pem + "\n" for pem in windows_pems]
    text = certifi_text if certifi_text.endswith("\n") else certifi_text + "\n"
    if extra:
        text += _WINDOWS_HEADER + "".join(extra)
    return text


def write_bundle(path: Path, text: str) -> bool:
    """Write ``text`` to ``path`` unless it already holds exactly that. True when written."""
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def _from_environment(env: Mapping[str, str]) -> CaBundle | None:
    for name in ENV_ORDER:
        value = env.get(name)
        if not value:
            continue
        path = Path(value)
        if not path.is_file():
            raise TlsError(f"{name} is set but is not a file: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        return CaBundle(
            path=path, source=name, certificates=count_certificates(text), windows_roots=0
        )
    return None


def ca_bundle(data_dir: Path, env: Mapping[str, str] | None = None) -> CaBundle:
    """The bundle every Google client should verify against. Makes no network call.

    Honours ``HTTPLIB2_CA_CERTS`` then ``REQUESTS_CA_BUNDLE``; otherwise (re)writes
    ``<data_dir>/ca-bundle.pem`` from certifi plus the Windows store and returns that.
    """
    found = _from_environment(os.environ if env is None else env)
    if found is not None:
        return found
    certifi_text = certifi_pem()
    windows = windows_root_pems()
    path = data_dir / BUNDLE_FILENAME
    write_bundle(path, combine(certifi_text, windows))
    return CaBundle(
        path=path,
        source=SOURCE_COMBINED if windows else SOURCE_CERTIFI,
        certificates=count_certificates(certifi_text) + len(windows),
        windows_roots=len(windows),
    )
