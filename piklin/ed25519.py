"""Checking an Ed25519 signature, with nothing but Python.

Releases are signed with Vezzu Studio's Ed25519 key. On Linux the update
helper checks the signature with openssl; the LibreSSL a Mac ships cannot
check Ed25519 at all, so this does it instead - the verification algorithm
of RFC 8032, section 5.1.7. It is slow as cryptography goes (a fraction of a
second) and only ever checks one small signed checksum per update.

Only verification lives here: nothing in Piklin ever signs.
"""
from __future__ import annotations

import base64
import hashlib

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


# Points in extended coordinates (X, Y, Z, T).
def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s: int, point):
    result = (0, 1, 1, 0)
    while s:
        if s & 1:
            result = _add(result, point)
        point = _add(point, point)
        s >>= 1
    return result


def _equal(a, b) -> bool:
    return ((a[0] * b[2] - b[0] * a[2]) % _P == 0
            and (a[1] * b[2] - b[1] * a[2]) % _P == 0)


def _decompress(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


_GY = 4 * _inv(5) % _P
_BASE = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True when ``signature`` is ``message`` signed by ``public_key``."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(),
                       "little") % _Q
    return _equal(_mul(s, _BASE), _add(r, _mul(h, a)))


# The DER prefix of an Ed25519 public key: SEQUENCE { AlgorithmIdentifier
# { 1.3.101.112 }, BIT STRING } followed by the 32 bytes of the key.
_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def public_key_from_pem(text: str) -> bytes:
    """The raw 32-byte key from a PEM "PUBLIC KEY" block, as openssl writes it."""
    body = "".join(line.strip() for line in text.splitlines()
                   if line.strip() and not line.startswith("-----"))
    der = base64.b64decode(body)
    if len(der) != 44 or not der.startswith(_SPKI_PREFIX):
        raise ValueError("not an Ed25519 public key")
    return der[len(_SPKI_PREFIX):]
