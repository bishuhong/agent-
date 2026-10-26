"""Generate separate development-only keys without printing or overwriting them."""

import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from etl_agent.shared.canonical import encode_url


def main() -> None:
    folder = Path(__file__).resolve().parents[1] / ".runtime"
    folder.mkdir(exist_ok=True)
    target = folder / "development-secrets.env"
    key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    with target.open("x", encoding="utf-8") as output:
        output.write(f"ETL_SESSION_KEY={secrets.token_urlsafe(48)}\n")
        output.write(f"ETL_CURSOR_KEY={secrets.token_urlsafe(48)}\n")
        output.write(f"ETL_CAPABILITY_PRIVATE_KEY={encode_url(key)}\n")
        output.write("ETL_CAPABILITY_KEY_ID=development-v1\n")
    print(f"开发密钥已保存：{target}。请通过本地环境配置加载，不提交到版本库。")


if __name__ == "__main__":
    main()
