"""Prints a VAPID key pair for web push. Run once locally; put the values in the backend environment.

    python backend/scripts/generate_vapid_keys.py

VAPID_PRIVATE_KEY is a secret: store it only in the hosting environment, never in the repository or in chat.
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
private = key.private_numbers().private_value.to_bytes(32, "big")
public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print("VAPID_PUBLIC_KEY=" + b64url(public))
print("VAPID_PRIVATE_KEY=" + b64url(private))
print("VAPID_SUBJECT=mailto:destek@kaistrade.com")
