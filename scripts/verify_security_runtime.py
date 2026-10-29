"""Verify the release image's Python and native certificate/Brotli fixes offline."""

import json
import platform
import ssl
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import certifi
from cryptography import x509
from cryptography.x509.oid import NameOID
from pip._vendor import certifi as pip_certifi


def verify() -> dict[str, object]:
    """Fail a build with stale certificate stores or a vulnerable Debian library."""
    if sys.version_info[:3] != (3, 12, 16):
        raise RuntimeError("Expected the reviewed Python 3.12.16 runtime")
    if version("pip") != "26.2.2" or version("certifi") != "2026.10.23":
        raise RuntimeError("Expected the reviewed pip/certifi fixes")

    bundle_paths = {"certifi": certifi.where(), "pip": pip_certifi.where()}
    native_bundle = ssl.get_default_verify_paths().cafile
    if platform.system() == "Linux":
        if not native_bundle:
            raise RuntimeError("System CA bundle unavailable")
        bundle_paths["system"] = native_bundle
    rejected_root = "emSign ECC Root CA - G3"
    counts = {}
    for label, path in bundle_paths.items():
        bundle = Path(path).read_bytes()
        if not bundle.strip():
            raise RuntimeError(f"Empty certificate bundle: {label}")
        certificates = x509.load_pem_x509_certificates(bundle)
        if not certificates:
            raise RuntimeError(f"Empty certificate bundle: {label}")
        if any(
            attribute.value == rejected_root
            for certificate in certificates
            for attribute in certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        ):
            raise RuntimeError(f"Compromised CA still trusted by {label}")
        counts[label] = len(certificates)

    for name in ("Brotli", "brotlicffi", "litellm"):
        try:
            version(name)
        except PackageNotFoundError:
            continue
        raise RuntimeError(f"Unexpected package in the reviewed production graph: {name}")

    native_packages = {}
    if platform.system() == "Linux":
        release = platform.freedesktop_os_release()
        if release.get("ID") != "debian" or release.get("VERSION_CODENAME") != "trixie":
            raise RuntimeError("Review native package security floors for this base image")
        floors = {
            "ca-certificates": "20250419+deb13u1",
            "libbrotli1": "1.1.0-2+deb13u1",
            "libssl3t64": "3.5.7-1~deb13u3",
        }
        for package, floor in floors.items():
            installed = subprocess.check_output(
                ["dpkg-query", "--show", "--showformat=${Version}", package], text=True
            ).strip()
            subprocess.run(["dpkg", "--compare-versions", installed, "ge", floor], check=True)
            native_packages[package] = installed

    return {
        "python": platform.python_version(),
        "openssl": ssl.OPENSSL_VERSION,
        "pip": version("pip"),
        "certifi": version("certifi"),
        "certificate_counts": counts,
        "native_packages": native_packages,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
