"""Prints a VAPID keypair in the exact form `PushSettings` expects.

    python -m scripts.generate_vapid_keys

**A script rather than a line in the docs**, because the obvious command --
`python -m py_vapid --gen` -- produces PEM *files*, and a PEM pasted into
`PUSH__VAPID_PRIVATE_KEY` fails only at the first send attempt, with an ASN.1
parsing error that names nothing about VAPID. `pywebpush` passes a string
private key to `py_vapid.Vapid.from_string`, which base64url-decodes it and
wants the raw 32-byte scalar. This prints that.

The public key is the uncompressed P-256 point (65 bytes, base64url) that the
browser's `applicationServerKey` requires. The two must come from the same
keypair or every push is signed with a key the push service will not accept
for that subscription.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())

    private_scalar = key.private_numbers().private_value.to_bytes(32, "big")
    public_point = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    print("# Add these to your .env -- keep the private key server-side only.")
    print(f"PUSH__VAPID_PUBLIC_KEY={_b64url(public_point)}")
    print(f"PUSH__VAPID_PRIVATE_KEY={_b64url(private_scalar)}")
    print("PUSH__VAPID_SUBJECT=mailto:ops@example.com")


if __name__ == "__main__":
    main()
