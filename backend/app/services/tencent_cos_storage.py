"""Tencent COS 存储服务。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from io import BytesIO
import mimetypes
from typing import Optional
from urllib.parse import quote

import httpx

from app.config import settings
from app.logger import get_logger

logger = get_logger(__name__)


@dataclass
class COSObjectMetadata:
    bucket: str
    region: str
    object_key: str
    url: str
    etag: Optional[str]
    content_length: Optional[int]
    content_type: Optional[str]


class TencentCOSStorageService:
    """Tencent COS 上传下载服务。

    优先使用 `qcloud_cos` SDK；不可用时退回到手动签名的 HTTP 请求。
    """

    def __init__(self) -> None:
        self.secret_id = (settings.TENCENT_COS_SECRET_ID or "").strip()
        self.secret_key = (settings.TENCENT_COS_SECRET_KEY or "").strip()
        self.bucket = (settings.TENCENT_COS_BUCKET or "").strip()
        self.region = (settings.TENCENT_COS_REGION or "").strip()
        self.public_domain = (settings.TENCENT_COS_CDN_DOMAIN or settings.TENCENT_COS_DOMAIN or "").strip().rstrip("/")
        self.object_prefix = (settings.TENCENT_COS_PREFIX or "").strip().strip("/")

    def is_enabled(self) -> bool:
        return bool(self.secret_id and self.secret_key and self.bucket and self.region)

    def build_object_key(self, *parts: str) -> str:
        cleaned = [part.strip("/") for part in parts if part and part.strip("/")]
        if self.object_prefix:
            cleaned.insert(0, self.object_prefix)
        return "/".join(cleaned)

    def guess_content_type(self, filename: str, default: str = "application/octet-stream") -> str:
        guessed, _ = mimetypes.guess_type(filename)
        return guessed or default

    def public_url(self, object_key: str) -> str:
        object_path = quote(object_key.lstrip("/"), safe="/")
        if self.public_domain:
            return f"{self.public_domain}/{object_path}"
        return f"https://{self.bucket}.cos.{self.region}.myqcloud.com/{object_path}"

    def _object_host(self) -> str:
        return f"{self.bucket}.cos.{self.region}.myqcloud.com"

    def _object_url(self, object_key: str) -> str:
        return f"https://{self._object_host()}/{quote(object_key.lstrip('/'), safe='/')}"

    async def upload_bytes(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> COSObjectMetadata:
        if not self.is_enabled():
            raise RuntimeError("Tencent COS 未配置")

        sdk_error: Exception | None = None
        if self._sdk_available():
            try:
                return await self._upload_with_sdk(object_key=object_key, content=content, content_type=content_type)
            except Exception as exc:
                sdk_error = exc
                logger.warning("COS SDK 上传失败，降级到手动签名上传: key=%s error=%s", object_key, exc)

        try:
            return await self._upload_with_http(object_key=object_key, content=content, content_type=content_type)
        except Exception:
            if sdk_error is not None:
                logger.error("COS SDK 与 HTTP 回退均失败: key=%s", object_key, exc_info=True)
            raise

    async def download_bytes(self, *, object_key: str) -> tuple[bytes, str | None]:
        if not self.is_enabled():
            raise RuntimeError("Tencent COS 未配置")

        if self._sdk_available():
            try:
                return await self._download_with_sdk(object_key=object_key)
            except Exception as exc:
                logger.warning("COS SDK 下载失败，降级到手动签名下载: key=%s error=%s", object_key, exc)

        return await self._download_with_http(object_key=object_key)

    async def delete_object(self, *, object_key: str) -> None:
        if not self.is_enabled():
            raise RuntimeError("Tencent COS 未配置")

        if self._sdk_available():
            try:
                await self._delete_with_sdk(object_key=object_key)
                return
            except Exception as exc:
                logger.warning("COS SDK 删除失败，降级到手动签名删除: key=%s error=%s", object_key, exc)

        await self._delete_with_http(object_key=object_key)

    async def get_read_url(self, *, object_key: str, expires_seconds: int = 600) -> str:
        if self.public_domain:
            return self.public_url(object_key)
        if self._sdk_available():
            try:
                return await self._get_read_url_with_sdk(object_key=object_key, expires_seconds=expires_seconds)
            except Exception as exc:
                logger.warning("COS SDK 签名 URL 生成失败: key=%s error=%s", object_key, exc)
        raise RuntimeError("当前 COS 配置无法生成可直接重定向的读 URL")

    def _sdk_available(self) -> bool:
        try:
            import qcloud_cos  # noqa: F401
        except ImportError:
            return False
        return True

    async def _upload_with_sdk(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> COSObjectMetadata:
        from qcloud_cos import CosConfig, CosS3Client

        config = CosConfig(Region=self.region, SecretId=self.secret_id, SecretKey=self.secret_key, Token=None, Scheme="https")
        client = CosS3Client(config)

        def _put() -> dict:
            return client.put_object(
                Bucket=self.bucket,
                Body=BytesIO(content),
                Key=object_key,
                ContentType=content_type,
                EnableMD5=False,
            )

        result = await self._run_blocking(_put)
        etag = None
        if isinstance(result, dict):
            etag = result.get("ETag") or result.get("etag")
        return COSObjectMetadata(
            bucket=self.bucket,
            region=self.region,
            object_key=object_key,
            url=self.public_url(object_key),
            etag=etag.strip('"') if isinstance(etag, str) else None,
            content_length=len(content),
            content_type=content_type,
        )

    async def _download_with_sdk(self, *, object_key: str) -> tuple[bytes, str | None]:
        from qcloud_cos import CosConfig, CosS3Client

        config = CosConfig(Region=self.region, SecretId=self.secret_id, SecretKey=self.secret_key, Token=None, Scheme="https")
        client = CosS3Client(config)

        def _get() -> tuple[bytes, str | None]:
            response = client.get_object(Bucket=self.bucket, Key=object_key)
            body = response["Body"].get_raw_stream().read()
            headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
            return body, headers.get("content-type")

        return await self._run_blocking(_get)

    async def _delete_with_sdk(self, *, object_key: str) -> None:
        from qcloud_cos import CosConfig, CosS3Client

        config = CosConfig(Region=self.region, SecretId=self.secret_id, SecretKey=self.secret_key, Token=None, Scheme="https")
        client = CosS3Client(config)

        def _delete() -> None:
            client.delete_object(Bucket=self.bucket, Key=object_key)

        await self._run_blocking(_delete)

    async def _get_read_url_with_sdk(self, *, object_key: str, expires_seconds: int) -> str:
        from qcloud_cos import CosConfig, CosS3Client

        config = CosConfig(Region=self.region, SecretId=self.secret_id, SecretKey=self.secret_key, Token=None, Scheme="https")
        client = CosS3Client(config)

        def _sign() -> str:
            return client.get_presigned_url(
                Method="GET",
                Bucket=self.bucket,
                Key=object_key,
                Expired=expires_seconds,
            )

        return await self._run_blocking(_sign)

    async def _upload_with_http(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> COSObjectMetadata:
        headers = {
            "Host": self._object_host(),
            "Content-Type": content_type,
            "Content-Length": str(len(content)),
        }
        authorization = self._build_authorization(
            method="PUT",
            object_key=object_key,
            headers=headers,
        )
        headers["Authorization"] = authorization

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            response = await client.put(self._object_url(object_key), content=content, headers=headers)
            response.raise_for_status()

        return COSObjectMetadata(
            bucket=self.bucket,
            region=self.region,
            object_key=object_key,
            url=self.public_url(object_key),
            etag=(response.headers.get("ETag") or "").strip('"') or None,
            content_length=len(content),
            content_type=content_type,
        )

    async def _download_with_http(self, *, object_key: str) -> tuple[bytes, str | None]:
        headers = {"Host": self._object_host()}
        headers["Authorization"] = self._build_authorization(
            method="GET",
            object_key=object_key,
            headers=headers,
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            response = await client.get(self._object_url(object_key), headers=headers)
            response.raise_for_status()
        return response.content, response.headers.get("Content-Type")

    async def _delete_with_http(self, *, object_key: str) -> None:
        headers = {"Host": self._object_host()}
        headers["Authorization"] = self._build_authorization(
            method="DELETE",
            object_key=object_key,
            headers=headers,
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            response = await client.delete(self._object_url(object_key), headers=headers)
            response.raise_for_status()

    def _build_authorization(
        self,
        *,
        method: str,
        object_key: str,
        headers: dict[str, str],
    ) -> str:
        start = int(datetime.now(timezone.utc).timestamp()) - 60
        end = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        sign_time = f"{start};{end}"
        key_time = sign_time

        header_items = []
        for key, value in headers.items():
            lower_key = key.lower().strip()
            header_items.append((lower_key, quote(str(value).strip(), safe="-_.~")))
        header_items.sort(key=lambda item: item[0])
        header_list = ";".join(key for key, _ in header_items)
        http_headers = "&".join(f"{key}={value}" for key, value in header_items)

        http_string = "\n".join(
            [
                method.lower(),
                f"/{object_key.lstrip('/')}",
                "",
                http_headers,
                "",
            ]
        )
        sha1 = hashlib.sha1(http_string.encode("utf-8")).hexdigest()
        string_to_sign = "\n".join(["sha1", sign_time, sha1, ""])
        sign_key = hmac.new(self.secret_key.encode("utf-8"), key_time.encode("utf-8"), hashlib.sha1).hexdigest()
        signature = hmac.new(sign_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).hexdigest()
        return (
            f"q-sign-algorithm=sha1&q-ak={self.secret_id}&q-sign-time={sign_time}"
            f"&q-key-time={key_time}&q-header-list={header_list}&q-url-param-list=&q-signature={signature}"
        )

    async def _run_blocking(self, func):
        import asyncio

        return await asyncio.to_thread(func)


tencent_cos_storage = TencentCOSStorageService()
