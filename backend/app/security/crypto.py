"""Password hashing, JWT, secret encryption and log redaction."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken
from pwdlib import PasswordHash

from app.config import get_settings

_hasher = PasswordHash.recommended()  # argon2id


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password, password_hash)
    except Exception:  # noqa: BLE001 — must never raise
        return False


def create_access_token(user_id: int, token_version: int) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tv": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=s.jwt_expire_minutes),
        "typ": "access",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    s = get_settings()
    try:
        data = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == "access" else None


# ---------- secrets at rest ----------
def _fernet() -> Fernet:
    return Fernet(get_settings().app_encryption_key.encode())


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Stored credential cannot be decrypted (wrong APP_ENCRYPTION_KEY?)") from e


def secret_hint(plaintext: str) -> str:
    """Only the last 4 characters are ever shown after saving."""
    return "…" + plaintext[-4:] if len(plaintext) >= 8 else "…"


# ---------- redaction ----------
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*)[^\s'\"]{4,}"),
    re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"),
]
_SENSITIVE_KEYS = re.compile(r"(?i)(password|secret|token|api[_-]?key|authorization|credential)")
# Shapes that are a credential with little doubt (provider keys, bearer tokens, URLs carrying a password). The
# `password = …` heuristic is left out on purpose: it also matches ordinary code and prose.
_CREDENTIAL_SHAPES = [p for p in _SECRET_PATTERNS if "password|passwd" not in p.pattern]


def contains_credential(text: str) -> bool:
    """True when the text contains something shaped like a real credential."""
    return any(p.search(text or "") for p in _CREDENTIAL_SHAPES)


def redact(text: str, extra_secrets: list[str] | None = None) -> str:
    out = text
    for s in extra_secrets or []:
        if s and len(s) >= 4:
            out = out.replace(s, "[REDACTED]")
    for pat in _SECRET_PATTERNS:
        if pat.groups:
            out = pat.sub(lambda m: m.group(1) + "[REDACTED]", out)
        else:
            out = pat.sub("[REDACTED]", out)
    return out


def redact_obj(obj: Any) -> Any:
    """Recursively redact a JSON-like object: sensitive keys are masked, strings scanned."""
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if _SENSITIVE_KEYS.search(str(k)) and v not in (None, "") else redact_obj(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj
