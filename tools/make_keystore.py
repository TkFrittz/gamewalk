"""Generate the APK signing keystore.

Run once. The result is committed to the repo, which deserves an explanation
rather than a shrug:

Android refuses to install an APK over an existing one signed by a different
key. So the signing key has to stay the same across builds, or every update
means uninstall-then-reinstall and losing your settings. CI runners start
empty, so the key has to come from somewhere -- either a repository secret you
configure by hand, or a file in the repo.

For a personal app you sideload from your own Releases page, the committed key
is the right trade. It is not protecting a Play Store listing or an identity;
its only job is to be *stable*. The one thing it costs: someone who cloned this
repo could build an APK that installs over yours as an "update", which matters
only if you install APKs from somewhere other than your own releases.

To move to a secret instead, see the note in .github/workflows/android.yml.

    python tools/make_keystore.py
"""

from __future__ import annotations

import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

OUT = Path(__file__).resolve().parent.parent / "app" / "gamewalk.p12"
ALIAS = "gamewalk"
PASSWORD = b"gamewalk"
YEARS = 30


def main() -> int:
    if OUT.exists():
        print(f"{OUT} already exists. Delete it first if you really mean to "
              f"replace it -- a new key breaks updates for anyone who already "
              f"installed the app.")
        return 1

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "GameWalk"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "GameWalk"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # Android rejects APKs whose signing certificate expires before the
        # APK would; 30 years is the conventional margin.
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365 * YEARS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                       critical=True)
        .sign(key, hashes.SHA256())
    )

    blob = pkcs12.serialize_key_and_certificates(
        name=ALIAS.encode(),
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(PASSWORD),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(blob)

    print(f"wrote {OUT} ({len(blob)} bytes)")
    print(f"  alias    {ALIAS}")
    print(f"  password {PASSWORD.decode()}")
    print(f"  expires  {cert.not_valid_after_utc.date()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
