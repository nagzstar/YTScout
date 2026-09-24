"""TLS trust: the combined CA bundle, the environment override, and the clients using it.

No network. The Windows-store test runs only on Windows; the rest feed ``combine`` fake
PEM text so they pass anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import certifi
import pytest

from ytscout.youtube import tls
from ytscout.youtube.analytics import GoogleAnalyticsTransport
from ytscout.youtube.transport import GoogleTransport

CERT_A = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"
CERT_B = "-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----"  # no trailing newline


# --- combine and write ---------------------------------------------------------------------


def test_combine_keeps_certifi_first_and_appends_windows_roots() -> None:
    text = tls.combine(CERT_A, [CERT_B])
    assert text.startswith(CERT_A)
    assert text.endswith(CERT_B + "\n")
    assert tls.count_certificates(text) == 2


def test_combine_without_windows_roots_is_just_certifi() -> None:
    assert tls.combine(CERT_A, []) == CERT_A
    assert tls.combine(CERT_B, []) == CERT_B + "\n"


def test_write_bundle_only_rewrites_when_content_changes(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "ca-bundle.pem"
    assert tls.write_bundle(path, CERT_A) is True
    assert tls.write_bundle(path, CERT_A) is False
    assert tls.write_bundle(path, CERT_A + CERT_B) is True
    assert path.read_text(encoding="utf-8") == CERT_A + CERT_B


# --- the bundle on this machine ------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows certificate store")
def test_combined_bundle_holds_certifi_plus_a_windows_root(tmp_path: Path) -> None:
    bundle = tls.ca_bundle(tmp_path, env={})
    assert bundle.path == tmp_path / tls.BUNDLE_FILENAME
    assert bundle.source == tls.SOURCE_COMBINED
    assert bundle.windows_roots >= 1
    text = bundle.path.read_text(encoding="utf-8")
    certifi_text = Path(certifi.where()).read_text(encoding="utf-8")
    assert text.startswith(certifi_text.rstrip("\n"))
    assert tls.count_certificates(text) == bundle.certificates
    assert bundle.certificates == tls.count_certificates(certifi_text) + bundle.windows_roots
    assert "Windows root store" in bundle.describe()


def test_ca_bundle_falls_back_to_certifi_without_a_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tls, "windows_root_pems", list)
    bundle = tls.ca_bundle(tmp_path, env={})
    assert bundle.source == tls.SOURCE_CERTIFI
    assert bundle.windows_roots == 0
    assert bundle.path.read_text(encoding="utf-8") == Path(certifi.where()).read_text(
        encoding="utf-8"
    )


def test_windows_root_pems_keeps_only_server_auth_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    der_a, der_b, der_c = b"\x30\x01", b"\x30\x02", b"\x30\x03"
    entries = [
        (der_a, "x509_asn", True),
        (der_b, "x509_asn", frozenset({tls._SERVER_AUTH_OID, "1.3.6.1.5.5.7.3.3"})),
        (der_c, "x509_asn", frozenset({"1.3.6.1.5.5.7.3.3"})),  # code signing only
        (der_a, "x509_asn", True),  # duplicate
        (b"\x30\x04", "pkcs_7_asn", True),  # not a bare certificate
    ]
    monkeypatch.setattr(tls.ssl, "enum_certificates", lambda store: entries, raising=False)
    pems = tls.windows_root_pems()
    assert len(pems) == 2
    assert all(p.startswith("-----BEGIN CERTIFICATE-----") for p in pems)


# --- environment override ------------------------------------------------------------------


def test_httplib2_env_var_wins_and_is_not_rewritten(tmp_path: Path) -> None:
    own = tmp_path / "mine.pem"
    own.write_text(CERT_A + CERT_B, encoding="utf-8")
    bundle = tls.ca_bundle(tmp_path / "data", env={tls.ENV_HTTPLIB2: str(own)})
    assert bundle.path == own
    assert bundle.source == tls.ENV_HTTPLIB2
    assert bundle.certificates == 2
    assert bundle.from_environment
    assert not (tmp_path / "data" / tls.BUNDLE_FILENAME).exists()
    assert "from HTTPLIB2_CA_CERTS" in bundle.describe()


def test_requests_env_var_is_the_second_choice(tmp_path: Path) -> None:
    own = tmp_path / "mine.pem"
    own.write_text(CERT_A, encoding="utf-8")
    bundle = tls.ca_bundle(tmp_path, env={tls.ENV_REQUESTS: str(own), tls.ENV_HTTPLIB2: ""})
    assert bundle.source == tls.ENV_REQUESTS


def test_env_var_pointing_nowhere_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(tls.TlsError, match="HTTPLIB2_CA_CERTS"):
        tls.ca_bundle(tmp_path, env={tls.ENV_HTTPLIB2: str(tmp_path / "missing.pem")})


# --- the clients carry the bundle ----------------------------------------------------------


class _Built:
    def __init__(self) -> None:
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, name: str, version: str, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        return object()


def test_data_api_client_verifies_against_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import googleapiclient.discovery

    built = _Built()
    monkeypatch.setattr(googleapiclient.discovery, "build", built)
    bundle = tmp_path / "ca-bundle.pem"
    GoogleTransport("k", ca_certs=bundle)._youtube()
    (kwargs,) = built.kwargs
    assert kwargs["developerKey"] == "k"
    assert kwargs["http"].ca_certs == str(bundle)
    assert kwargs["http"].disable_ssl_certificate_validation is False


def test_analytics_client_verifies_against_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import googleapiclient.discovery

    built = _Built()
    monkeypatch.setattr(googleapiclient.discovery, "build", built)
    bundle = tmp_path / "ca-bundle.pem"
    credentials = object()
    GoogleAnalyticsTransport(credentials, ca_certs=bundle)._analytics()
    (kwargs,) = built.kwargs
    assert "credentials" not in kwargs
    authed = kwargs["http"]
    assert authed.credentials is credentials
    assert authed.http.ca_certs == str(bundle)
    assert authed.http.disable_ssl_certificate_validation is False


def test_token_refresh_session_verifies_against_the_bundle(tmp_path: Path) -> None:
    from ytscout.youtube.oauth import _session

    bundle = tmp_path / "ca-bundle.pem"
    assert _session(bundle).verify == str(bundle)
    assert _session(None).verify is True
