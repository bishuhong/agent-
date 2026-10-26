"""Standard scrypt password storage with bounded concurrent verification."""

import hashlib
import hmac
import secrets
import threading

from etl_agent.shared.canonical import decode_url, encode_url
from etl_agent.shared.errors import ApplicationError


class PasswordHasher:
    def __init__(self, n: int = 131072, r: int = 8, p: int = 1, parallelism: int = 2):
        if n < 131072 or n & (n - 1) or r < 8 or p < 1:
            raise ValueError("scrypt参数不符合安全基线")
        self.n, self.r, self.p = n, r, p
        self._gate = threading.BoundedSemaphore(parallelism)

    def _derive(self, password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
        # Restrict attacker-influenced stored parameters as well as live concurrency.
        if n < 131072 or n > 1048576 or n & (n - 1) or not 8 <= r <= 16 or not 1 <= p <= 4:
            raise ValueError("invalid stored scrypt parameters")
        if not self._gate.acquire(timeout=5):
            raise ApplicationError(
                "DEPENDENCY_UNAVAILABLE", "密码验证资源繁忙，请稍后重试", 503, retryable=True
            )
        try:
            return hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=n,
                r=r,
                p=p,
                maxmem=max(268435456, 256 * n * r),
                dklen=32,
            )
        finally:
            self._gate.release()

    def hash(self, password: str) -> str:
        if not 12 <= len(password) <= 128:
            raise ValueError("password length out of bounds")
        salt = secrets.token_bytes(16)
        value = self._derive(password, salt, self.n, self.r, self.p)
        return f"scrypt${self.n}${self.r}${self.p}${encode_url(salt)}${encode_url(value)}"

    def verify(self, password: str, encoded: str) -> bool:
        if not 12 <= len(password) <= 128:
            return False
        try:
            kind, n, r, p, salt, expected = encoded.split("$")
            if kind != "scrypt":
                return False
            salt_bytes = decode_url(salt)
            expected_bytes = decode_url(expected)
            if len(salt_bytes) < 16 or len(expected_bytes) != 32:
                return False
            actual = self._derive(password, salt_bytes, int(n), int(r), int(p))
            return hmac.compare_digest(actual, expected_bytes)
        except (ValueError, TypeError, ApplicationError) as exc:
            if isinstance(exc, ApplicationError) and exc.status_code == 503:
                raise
            return False
