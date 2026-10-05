"""``fcrm_common.objectstore``: the S3-compatible client, driven by fakes and a
botocore ``Stubber`` so no test opens a socket.
"""

from __future__ import annotations

import io
import logging
import threading
from typing import Any, Optional

import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from botocore.stub import Stubber
from fcrm_common.errors import NotFoundError, UpstreamUnavailableError
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.objectstore import (
    ObjectInfo,
    ObjectStore,
    build_client,
    client_config,
)
from fcrm_common.settings import S3Settings

BUCKET = "uploads"
KEY = "FCRM-2026-0151/doc-1/business_case_remittance_MX.pdf"


class _Body:
    """The ``Body`` of a ``get_object`` response."""

    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)
        self.closed = False

    def read(self) -> bytes:
        return self._stream.read()

    def close(self) -> None:
        self.closed = True


class _FakeS3:
    """An in-memory S3 client; ``fail`` raises on every call when set."""

    def __init__(self, fail: Optional[Exception] = None) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.fail = fail
        self.threads: list[str] = []
        self.bodies: list[_Body] = []

    def _enter(self) -> None:
        self.threads.append(threading.current_thread().name)
        if self.fail is not None:
            raise self.fail

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._enter()
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = {
            "Body": kwargs["Body"],
            "ContentType": kwargs["ContentType"],
        }
        return {"ETag": '"abc123"'}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self._enter()
        stored = self.objects.get((kwargs["Bucket"], kwargs["Key"]))
        if stored is None:
            raise _client_error("NoSuchKey", 404, "GetObject")
        body = _Body(stored["Body"])
        self.bodies.append(body)
        return {"Body": body, "ContentType": stored["ContentType"]}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self._enter()
        stored = self.objects.get((kwargs["Bucket"], kwargs["Key"]))
        if stored is None:
            raise _client_error("404", 404, "HeadObject")
        return {
            "ContentLength": len(stored["Body"]),
            "ContentType": stored["ContentType"],
            "ETag": '"abc123"',
        }

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self._enter()
        return {}

    def generate_presigned_url(self, ClientMethod: str, **kwargs: Any) -> str:  # noqa: N803
        return f"http://fake/{kwargs['Params']['Bucket']}?exp={kwargs['ExpiresIn']}"


def _client_error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "synthetic"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


def _settings(**overrides: Any) -> S3Settings:
    return S3Settings(
        s3_endpoint_url="http://minio.test:9000",
        s3_access_key="test-access",
        s3_secret_key="test-secret",  # type: ignore[arg-type]
        **overrides,
    )


async def test_put_then_get_round_trips_the_bytes() -> None:
    fake = _FakeS3()
    store = ObjectStore(fake)
    info = await store.put_bytes(BUCKET, KEY, b"%PDF-1.4 demo", "application/pdf")
    assert info == ObjectInfo(size=13, content_type="application/pdf", etag='"abc123"')
    assert await store.get_bytes(BUCKET, KEY) == b"%PDF-1.4 demo"


async def test_get_bytes_closes_the_response_body() -> None:
    fake = _FakeS3()
    store = ObjectStore(fake)
    await store.put_bytes(BUCKET, KEY, b"x", "text/plain")
    await store.get_bytes(BUCKET, KEY)
    assert [b.closed for b in fake.bodies] == [True]


async def test_head_reports_size_type_and_etag() -> None:
    store = ObjectStore(_FakeS3())
    await store.put_bytes(BUCKET, KEY, b"12345", "text/markdown")
    info = await store.head(BUCKET, KEY)
    assert info.size == 5
    assert info.content_type == "text/markdown"
    assert info.etag == '"abc123"'


async def test_blocking_calls_run_off_the_event_loop_thread() -> None:
    fake = _FakeS3()
    store = ObjectStore(fake)
    await store.put_bytes(BUCKET, KEY, b"x", "text/plain")
    await store.head(BUCKET, KEY)
    await store.ping(BUCKET)
    assert fake.threads
    assert threading.current_thread().name not in fake.threads


@pytest.mark.parametrize(
    ("method", "code"),
    [("get_bytes", "NoSuchKey"), ("head", "404")],
)
async def test_missing_object_maps_to_not_found(method: str, code: str) -> None:
    store = ObjectStore(_FakeS3())
    with pytest.raises(NotFoundError, match=KEY):
        await getattr(store, method)(BUCKET, KEY)
    assert code


async def test_no_such_bucket_maps_to_not_found() -> None:
    error = _client_error("NoSuchBucket", 404, "PutObject")
    store = ObjectStore(_FakeS3(fail=error))
    with pytest.raises(NotFoundError):
        await store.put_bytes("nope", KEY, b"x", "text/plain")


@pytest.mark.parametrize(
    "failure",
    [
        ReadTimeoutError(endpoint_url="http://minio.test:9000", error="read timed out"),
        EndpointConnectionError(endpoint_url="http://minio.test:9000"),
    ],
    ids=["read-timeout", "connection-refused"],
)
async def test_timeouts_and_connection_failures_map_to_unavailable(
    failure: Exception,
) -> None:
    store = ObjectStore(_FakeS3(fail=failure))
    with pytest.raises(UpstreamUnavailableError) as raised:
        await store.get_bytes(BUCKET, KEY)
    assert raised.value.extra["dependency"] == "s3"
    assert raised.value.__cause__ is failure


@pytest.mark.parametrize(
    ("code", "status"),
    [("ServiceUnavailable", 503), ("SlowDown", 503), ("SomethingOdd", 500)],
)
async def test_server_errors_map_to_unavailable(code: str, status: int) -> None:
    store = ObjectStore(_FakeS3(fail=_client_error(code, status, "HeadObject")))
    with pytest.raises(UpstreamUnavailableError) as raised:
        await store.head(BUCKET, KEY)
    assert raised.value.extra["dependency"] == "s3"


async def test_other_client_errors_are_logged_and_reraised_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    denied = _client_error("AccessDenied", 403, "PutObject")
    store = ObjectStore(_FakeS3(fail=denied))
    with caplog.at_level(logging.ERROR), pytest.raises(ClientError) as raised:
        await store.put_bytes(BUCKET, KEY, b"x", "text/plain")
    assert raised.value is denied
    logged = [
        r for r in caplog.records if r.getMessage() == "object store request refused"
    ]
    assert logged[0].__dict__["bucket"] == BUCKET
    assert logged[0].__dict__["key"] == KEY


async def test_object_bodies_are_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_body = b"SSN-LOOKING-PAYLOAD-4242"
    store = ObjectStore(_FakeS3(fail=_client_error("InternalError", 500, "PutObject")))
    with caplog.at_level(logging.DEBUG), pytest.raises(UpstreamUnavailableError):
        await store.put_bytes(BUCKET, KEY, secret_body, "text/plain")
    assert secret_body.decode() not in caplog.text


@pytest.mark.parametrize(
    ("bucket", "key"),
    [("", KEY), ("   ", KEY), (BUCKET, ""), (BUCKET, "k" * 1025)],
)
async def test_invalid_bucket_or_key_is_rejected_before_any_call(
    bucket: str, key: str
) -> None:
    fake = _FakeS3()
    store = ObjectStore(fake)
    with pytest.raises(FcrmValidationError):
        await store.get_bytes(bucket, key)
    assert fake.threads == []


async def test_stubber_checks_the_real_client_request_shape() -> None:
    client = build_client(_settings())
    with Stubber(client) as stubber:  # type: ignore[arg-type]
        stubber.add_response(
            "put_object",
            {"ETag": '"e1"'},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "Body": b"hello",
                "ContentType": "text/plain",
            },
        )
        stubber.add_client_error(
            "head_object", service_error_code="404", http_status_code=404
        )
        store = ObjectStore(client)
        info = await store.put_bytes(BUCKET, KEY, b"hello", "text/plain")
        assert info.etag == '"e1"'
        with pytest.raises(NotFoundError):
            await store.head(BUCKET, KEY)
        stubber.assert_no_pending_responses()


def test_presign_get_signs_locally_with_the_requested_ttl() -> None:
    store = ObjectStore.from_settings(_settings())
    url = store.presign_get(BUCKET, KEY, 300)
    assert url.startswith("http://minio.test:9000/uploads/FCRM-2026-0151/")
    assert "X-Amz-Expires=300" in url
    assert "X-Amz-Signature=" in url


@pytest.mark.parametrize("ttl", [0, -5, 7 * 24 * 3600 + 1])
def test_presign_get_rejects_a_ttl_outside_the_sigv4_window(ttl: int) -> None:
    store = ObjectStore(_FakeS3())
    with pytest.raises(FcrmValidationError):
        store.presign_get(BUCKET, KEY, ttl)


def test_build_client_carries_the_timeout_and_retry_policy() -> None:
    config = build_client(_settings()).meta.config  # type: ignore[attr-defined]
    assert config.connect_timeout == 2
    assert config.read_timeout == 30
    # botocore stores ``max_attempts`` (retries) as ``total_max_attempts``
    # (the first try plus the three retries).
    assert config.retries == {"mode": "standard", "total_max_attempts": 4}
    assert config.s3 == {"addressing_style": "path"}


def test_client_config_is_virtual_hosted_unless_path_style_is_asked_for() -> None:
    assert client_config().s3 in (None, {})
    assert client_config(path_style=True).s3 == {"addressing_style": "path"}


def test_s3_settings_default_to_the_buckets_ops_minio_init_creates() -> None:
    settings = S3Settings()
    assert settings.s3_endpoint_url is None
    assert (
        settings.s3_bucket_uploads,
        settings.s3_bucket_canonical,
        settings.s3_bucket_packs,
        settings.s3_bucket_vectors,
        settings.s3_bucket_jobs,
    ) == ("uploads", "canonical", "packs", "vectors", "jobs")


def test_s3_settings_read_the_compose_variable_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("S3_REGION", "eu-west-1")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "shh")
    monkeypatch.setenv("S3_BUCKET_PACKS", "packs-test")
    settings = S3Settings()
    assert settings.s3_endpoint_url == "http://minio:9000"
    assert settings.s3_region == "eu-west-1"
    assert settings.s3_access_key == "key"
    assert settings.s3_secret_key is not None
    assert settings.s3_secret_key.get_secret_value() == "shh"
    assert "shh" not in repr(settings)
    assert settings.s3_bucket_packs == "packs-test"
