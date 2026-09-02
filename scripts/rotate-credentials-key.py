"""Rotate the encryption key for platform credentials.

Run with the same REPOMESH_DATABASE_URL and key environment as the API. The
new key is written only after every row has been decrypted and re-encrypted in
one transaction.
"""

import argparse
import asyncio
import os
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select

from repomesh.modules.platform_config.crypto import credentials_key_path
from repomesh.modules.platform_config.store import PlatformCredentialRecord
from repomesh.persistence import Database
from repomesh.settings import get_settings


async def rotate(new_key_path: Path | None) -> None:
    old_configured = os.environ.get("REPOMESH_CREDENTIALS_ENCRYPTION_KEY", "").strip()
    old_key_path = credentials_key_path()
    old_key = (
        old_configured.encode("ascii")
        if old_configured
        else old_key_path.read_bytes().strip()
    )
    new_key = Fernet.generate_key()
    old_fernet = Fernet(old_key)
    new_fernet = Fernet(new_key)
    database = Database(get_settings().database_url)
    try:
        async with database.transaction() as session:
            records = (await session.execute(select(PlatformCredentialRecord))).scalars()
            for record in records:
                plaintext = old_fernet.decrypt(record.value_encrypted)
                record.value_encrypted = new_fernet.encrypt(plaintext)
    finally:
        await database.dispose()

    destination = new_key_path or old_key_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".new")
    temporary.write_bytes(new_key + b"\n")
    temporary.replace(destination)
    print(f"rotated platform credential encryption key: {destination}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--new-key-file", type=Path)
    args = parser.parse_args()
    asyncio.run(rotate(args.new_key_file))


if __name__ == "__main__":
    main()
