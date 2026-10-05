"""The S3-compatible object store client (MinIO locally, S3 on AWS).

``ObjectStore`` is the one place the platform touches object storage: S1
stores the uploaded documents, H3 reads them and writes the canonical
Markdown, H1/H2 spill large payloads to ``jobs``, S5 freezes pack snapshots.
It is deliberately small -- ``put_bytes``, ``get_bytes``, ``head``,
``presign_get`` and a readiness ``ping`` -- and has three properties every
caller can rely on:

* **Never blocks the event loop.** boto3 is synchronous, so each call runs
  through :func:`asyncio.to_thread`. The streaming body of ``get_object`` is
  read inside the same worker thread, so the read timeout applies to it too.
* **Every I/O has a timeout.** The real client is built with the
  :func:`client_config` below (2 s connect, 30 s read, three standard-mode
  attempts), so no call can hang a request or a job.
* **Failures map onto the platform hierarchy.** A missing key or bucket is
  :class:`fcrm_common.errors.NotFoundError`; a connection failure, a timeout
  or a 5xx is :class:`fcrm_common.errors.UpstreamUnavailableError` with
  ``dependency="s3"``. Any other ``ClientError`` (403, 400 ...) is a
  configuration or caller bug: it is logged with the bucket and key and
  re-raised unchanged rather than hidden behind a generic mapping.

The client is **injectable** (:class:`S3Client`), so unit tests drive the
store with a fake or a ``botocore.stub.Stubber`` and never open a socket.
Build the real client once per process (in the lifespan) with
:func:`build_client` and share the resulting :class:`ObjectStore`; boto3
clients are thread-safe. Object bodies are never logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final, Optional, Protocol, TypeVar

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError
from pydantic import BaseModel, ConfigDict

from fcrm_common.errors import (
    FcrmError,
    NotFoundError,
    UpstreamUnavailableError,
)
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.settings import S3Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: ``ClientError`` codes that mean "no such object or bucket".
_NOT_FOUND_CODES: Final[frozenset[str]] = frozenset(
    {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}
)
#: ``ClientError`` codes that mean "the store is struggling, try again".
_UNAVAILABLE_CODES: Final[frozenset[str]] = frozenset(
    {"SlowDown", "ServiceUnavailable", "InternalError", "RequestTimeout"}
)
_HTTP_SERVER_ERROR: Final = 500
#: An S3 object key is at most 1024 bytes of UTF-8.
MAX_KEY_BYTES: Final = 1024
#: SigV4 presigned URLs are valid for at most seven days.
MAX_PRESIGN_TTL_S: Final = 7 * 24 * 3600

CONNECT_TIMEOUT_S: Final = 2
READ_TIMEOUT_S: Final = 30
MAX_ATTEMPTS: Final = 3


class S3Client(Protocol):
    """The slice of the boto3 S3 client this module calls.

    Anything with these methods works: the real ``boto3`` client, a
    ``Stubber``-wrapped one, or a hand-written fake.
    """

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        """Store an object (``Bucket``, ``Key``, ``Body``, ``ContentType``)."""
        ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        """Fetch an object; the result's ``Body`` has ``read()``/``close()``."""
        ...

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        """Fetch an object's metadata without its body."""
        ...

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        """Check that a bucket exists and is reachable."""
        ...

    def generate_presigned_url(self, ClientMethod: str, **kwargs: Any) -> str:  # noqa: N803
        """Sign a URL locally (no network call)."""
        ...


class ObjectInfo(BaseModel):
    """What ``head`` and ``put_bytes`` report about an object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    size: int
    content_type: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[datetime] = None


def client_config(*, path_style: bool = False) -> Config:
    """The botocore :class:`~botocore.config.Config` every real client uses.

    Args:
        path_style: Force path-style addressing (``host/bucket/key``). MinIO
            at ``minio:9000`` needs it; AWS S3 does not.
    """
    return Config(
        connect_timeout=CONNECT_TIMEOUT_S,
        read_timeout=READ_TIMEOUT_S,
        retries={"max_attempts": MAX_ATTEMPTS, "mode": "standard"},
        signature_version="s3v4",
        s3={"addressing_style": "path"} if path_style else {},
    )


def build_client(settings: S3Settings) -> S3Client:
    """The real boto3 S3 client for ``settings``. Makes no network call.

    A set ``S3_ENDPOINT_URL`` means MinIO (path-style addressing, static keys
    from the settings); an empty one means AWS, where unset static keys fall
    through to the SDK's own credential chain.
    """
    secret = settings.s3_secret_key
    client: S3Client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=secret.get_secret_value() if secret else None,
        config=client_config(path_style=bool(settings.s3_endpoint_url)),
    )
    return client


def _validated(bucket: str, key: str) -> None:
    """Reject an empty bucket or key, or a key S3 would refuse."""
    if not bucket.strip():
        raise FcrmValidationError("object store bucket must not be empty")
    if not key or len(key.encode("utf-8")) > MAX_KEY_BYTES:
        raise FcrmValidationError(
            f"object key must be 1 to {MAX_KEY_BYTES} bytes", bucket=bucket
        )


def _error_code(error: ClientError) -> str:
    """The S3 error code of ``error`` (``""`` when the response has none)."""
    return str(error.response.get("Error", {}).get("Code", ""))


def _http_status(error: ClientError) -> int:
    """The HTTP status of ``error`` (``0`` when the response has none)."""
    meta = error.response.get("ResponseMetadata", {})
    return int(meta.get("HTTPStatusCode", 0))


class ObjectStore:
    """Async object storage over an injected S3 client.

    Args:
        client: A boto3-compatible S3 client (see :class:`S3Client`).
    """

    def __init__(self, client: S3Client) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: S3Settings) -> ObjectStore:
        """Build the real client from ``settings`` and wrap it."""
        return cls(build_client(settings))

    async def put_bytes(
        self, bucket: str, key: str, data: bytes, content_type: str
    ) -> ObjectInfo:
        """Store ``data`` at ``bucket``/``key`` and return what was stored.

        Raises:
            NotFoundError: The bucket does not exist.
            UpstreamUnavailableError: The store is unreachable or answered 5xx.
        """
        _validated(bucket, key)

        def _put() -> dict[str, Any]:
            return self._client.put_object(
                Bucket=bucket, Key=key, Body=data, ContentType=content_type
            )

        response = await self._call("put", bucket, key, _put)
        return ObjectInfo(
            size=len(data), content_type=content_type, etag=response.get("ETag")
        )

    async def get_bytes(self, bucket: str, key: str) -> bytes:
        """The whole object at ``bucket``/``key``.

        Raises:
            NotFoundError: No such object or bucket.
            UpstreamUnavailableError: The store is unreachable or answered 5xx.
        """
        _validated(bucket, key)

        def _get() -> bytes:
            response = self._client.get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            try:
                content: bytes = body.read()
            finally:
                body.close()
            return content

        return await self._call("get", bucket, key, _get)

    async def head(self, bucket: str, key: str) -> ObjectInfo:
        """Metadata of the object at ``bucket``/``key``.

        Raises:
            NotFoundError: No such object or bucket.
            UpstreamUnavailableError: The store is unreachable or answered 5xx.
        """
        _validated(bucket, key)

        def _head() -> dict[str, Any]:
            return self._client.head_object(Bucket=bucket, Key=key)

        response = await self._call("head", bucket, key, _head)
        return ObjectInfo(
            size=int(response.get("ContentLength", 0)),
            content_type=response.get("ContentType"),
            etag=response.get("ETag"),
            last_modified=response.get("LastModified"),
        )

    async def ping(self, bucket: str) -> None:
        """Readiness probe: the bucket exists and the store answers.

        Raises:
            NotFoundError: The bucket does not exist.
            UpstreamUnavailableError: The store is unreachable or answered 5xx.
        """

        def _ping() -> dict[str, Any]:
            return self._client.head_bucket(Bucket=bucket)

        await self._call("ping", bucket, "", _ping)

    def presign_get(self, bucket: str, key: str, ttl_s: int) -> str:
        """A time-limited ``GET`` URL for ``bucket``/``key``.

        Signing is local: no request is made, so this does not check that the
        object exists.

        Raises:
            ValidationError: An empty bucket or key, or ``ttl_s`` outside
                ``1..604800`` seconds.
        """
        _validated(bucket, key)
        if not 1 <= ttl_s <= MAX_PRESIGN_TTL_S:
            raise FcrmValidationError(
                f"presign ttl must be 1 to {MAX_PRESIGN_TTL_S} seconds",
                ttl_s=ttl_s,
            )
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl_s,
        )

    async def _call(
        self, operation: str, bucket: str, key: str, call: Callable[[], T]
    ) -> T:
        """Run the blocking ``call`` in a thread and map its failures."""
        try:
            return await asyncio.to_thread(call)
        except ClientError as error:
            mapped = self._map_client_error(operation, bucket, key, error)
            if mapped is None:
                raise
            raise mapped from error
        except (BotoConnectionError, HTTPClientError) as error:
            logger.error(
                "object store unreachable",
                extra={
                    "dependency": "s3",
                    "operation": operation,
                    "bucket": bucket,
                    "key": key,
                    "error": type(error).__name__,
                },
            )
            raise UpstreamUnavailableError(
                f"object store unreachable ({operation} {bucket})",
                dependency="s3",
            ) from error

    @staticmethod
    def _map_client_error(
        operation: str, bucket: str, key: str, error: ClientError
    ) -> Optional[FcrmError]:
        """The platform error for an S3 ``ClientError``, or ``None`` when it is
        neither "not found" nor "store unavailable" (the caller re-raises it).
        """
        code = _error_code(error)
        status = _http_status(error)
        context = {
            "dependency": "s3",
            "operation": operation,
            "bucket": bucket,
            "key": key,
            "code": code,
            "status": status,
        }
        if code in _NOT_FOUND_CODES or status == 404:  # noqa: PLR2004
            logger.info("object not found", extra=context)
            return NotFoundError(f"no object {bucket}/{key}")
        if code in _UNAVAILABLE_CODES or status >= _HTTP_SERVER_ERROR:
            logger.error("object store unavailable", extra=context)
            return UpstreamUnavailableError(
                f"object store answered {status or code} ({operation} {bucket})",
                dependency="s3",
            )
        logger.error("object store request refused", extra=context)
        return None
