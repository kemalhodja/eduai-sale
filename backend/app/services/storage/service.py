import asyncio
import uuid
from pathlib import Path

from app.config import settings

UPLOAD_DIR = Path("uploads")


def _s3_endpoint_valid(endpoint: str | None) -> bool:
    if not endpoint:
        return True
    lowered = endpoint.lower()
    if "your_" in lowered or "placeholder" in lowered or "example.com" in lowered:
        return False
    return endpoint.startswith("http://") or endpoint.startswith("https://")


def _create_s3_client():
    if not settings.s3_enabled:
        return None
    if not _s3_endpoint_valid(settings.s3_endpoint or None):
        return None
    try:
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
    except (ValueError, Exception):
        return None


class StorageService:
    def __init__(self):
        self._s3 = _create_s3_client()

    def _put_s3(self, key: str, data: bytes, extension: str) -> None:
        self._s3.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=f"image/{extension}",
        )

    def _get_s3_object(self, bucket: str, key: str) -> tuple[bytes, str]:
        obj = self._s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        media_type = obj.get("ContentType") or "image/jpeg"
        return body, media_type

    def _delete_s3_object(self, bucket: str, key: str) -> None:
        self._s3.delete_object(Bucket=bucket, Key=key)

    def _s3_location_from_path(self, stored_path: str) -> tuple[str, str] | None:
        if stored_path.startswith("s3://"):
            parts = stored_path.replace("s3://", "").split("/", 1)
            if len(parts) == 2:
                return parts[0], parts[1]
            return None

        public_url = settings.s3_public_url.rstrip("/")
        if public_url and stored_path.startswith(f"{public_url}/"):
            key = stored_path[len(public_url) + 1:]
            if key:
                return settings.s3_bucket, key
        return None

    async def upload(self, user_id: str, data: bytes, extension: str = "jpg") -> str:
        folder = "podcasts" if extension == "mp3" else "receipts"
        key = f"{folder}/{user_id}/{uuid.uuid4().hex}.{extension}"

        if self._s3:
            await asyncio.to_thread(self._put_s3, key, data, extension)
            # GUVENLIK: nesneler private tutulur; stored_path olarak daima s3://
            # formati kullanilir. Erisim yalnizca kisa TTL'li presigned URL ile.
            # (Eski davranis S3_PUBLIC_URL altinda herkese acik link donduruyordu.)
            return f"s3://{settings.s3_bucket}/{key}"

        user_dir = UPLOAD_DIR / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        filepath = user_dir / key.split("/")[-1]
        filepath.write_bytes(data)
        return str(filepath)

    async def get_url(self, stored_path: str) -> str:
        # Public URL'e kaydedilmis ESKI kayitlar icin: bucket/key cözüp
        # presigned URL uret (gerkese acik linke yonlendirme yapma).
        legacy_location = self._s3_location_from_path(stored_path) if stored_path.startswith("http") else None
        if legacy_location and self._s3:
            bucket, key = legacy_location
            return await asyncio.to_thread(
                self._s3.generate_presigned_url,
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=3600,
            )
        if stored_path.startswith("http"):
            return stored_path
        if stored_path.startswith("s3://") and self._s3:
            parts = stored_path.replace("s3://", "").split("/", 1)
            if len(parts) == 2:
                return await asyncio.to_thread(
                    self._s3.generate_presigned_url,
                    "get_object",
                    Params={"Bucket": parts[0], "Key": parts[1]},
                    ExpiresIn=3600,
                )
        return stored_path

    async def read_bytes(self, stored_path: str) -> tuple[bytes, str]:
        if stored_path.startswith("http"):
            raise FileNotFoundError(stored_path)

        if stored_path.startswith("s3://") and self._s3:
            parts = stored_path.replace("s3://", "").split("/", 1)
            if len(parts) != 2:
                raise FileNotFoundError(stored_path)
            return await asyncio.to_thread(self._get_s3_object, parts[0], parts[1])

        path = Path(stored_path)
        if not path.is_absolute():
            candidate = UPLOAD_DIR / stored_path if not stored_path.startswith("uploads/") else Path(stored_path)
            if candidate.exists():
                path = candidate
        if not path.exists():
            raise FileNotFoundError(stored_path)

        media_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        if path.suffix.lower() == ".mp3":
            media_type = "audio/mpeg"
        return path.read_bytes(), media_type

    async def delete(self, stored_path: str | None) -> None:
        if not stored_path:
            return

        s3_location = self._s3_location_from_path(stored_path)
        if s3_location and self._s3:
            bucket, key = s3_location
            await asyncio.to_thread(self._delete_s3_object, bucket, key)
            return

        if stored_path.startswith("http"):
            return

        path = Path(stored_path)
        if not path.is_absolute():
            candidate = UPLOAD_DIR / stored_path if not stored_path.startswith("uploads/") else Path(stored_path)
            if candidate.exists():
                path = candidate
        if path.exists() and path.is_file():
            path.unlink()
