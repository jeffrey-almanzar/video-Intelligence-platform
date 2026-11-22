"""S3-compatible object storage (SeaweedFS locally, AWS S3 in the cloud).

boto3 is synchronous; callers run these methods via `run_blocking` so the event loop stays free.
"""

import hashlib
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

from vip_ai.config import Settings

_CHUNK = 8 * 1024 * 1024


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        secret = settings.s3_secret_access_key
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=secret.get_secret_value() if secret else None,
            config=Config(
                s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
                retries={"max_attempts": 5, "mode": "standard"},
                # Same reasoning as the API client: no SDK-computed checksums on requests.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def download_with_sha256(self, key: str, dest: Path) -> tuple[int, bytes]:
        """Streams an object to disk while hashing it. Returns (size, sha256 digest)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + ".part")
        digest = hashlib.sha256()
        size = 0
        body = self._client.get_object(Bucket=self.bucket, Key=key)["Body"]
        with partial.open("wb") as out:
            for chunk in iter(lambda: body.read(_CHUNK), b""):
                digest.update(chunk)
                out.write(chunk)
                size += len(chunk)
        partial.replace(dest)  # atomic: a crash never leaves a truncated file at `dest`
        return size, digest.digest()

    def download(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + ".part")
        self._client.download_file(self.bucket, key, str(partial))
        partial.replace(dest)

    def upload(self, src: Path, key: str, content_type: str) -> int:
        """Uploads (overwriting) a file; deterministic keys make stage reruns idempotent."""
        self._client.upload_file(
            str(src), self.bucket, key, ExtraArgs={"ContentType": content_type}
        )
        return src.stat().st_size

    def size(self, key: str) -> int | None:
        try:
            return int(self._client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except self._client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NotFound", "NoSuchKey"}:
                return None
            raise
