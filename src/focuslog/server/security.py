"""Password hashing and login tokens, using only the standard library."""

import hashlib
import hmac
import secrets

# scrypt cost settings (N, r, p). Stored with each hash so they can be raised later.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return "scrypt$%d$%d$%d$%s$%s" % (SCRYPT_N, SCRYPT_R, SCRYPT_P, salt.hex(), digest.hex())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    # compare_digest takes the same time whether or not the strings match
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # Tokens are long random strings, so a plain SHA-256 is enough. We store
    # only the hash: a leaked database doesn't hand out working logins.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
