"""Tests for ``core/encoding.py`` — ``EncodingDetector`` never raises, never NULs."""

from __future__ import annotations

import codecs

from evoq_ingestion.core.encoding import EncodingDetector


class TestBom:
    def test_utf8_sig_bom(self):
        detector = EncodingDetector()
        result = detector.detect(codecs.BOM_UTF8 + b"hello")
        assert result.encoding == "utf-8-sig"
        assert result.bom is True

    def test_utf16_le_bom(self):
        detector = EncodingDetector()
        result = detector.detect("hello".encode("utf-16-le-bom" if False else "utf-16"))
        assert result.encoding in ("utf-16-le", "utf-16-be")
        assert result.bom is True

    def test_decode_strips_bom(self):
        detector = EncodingDetector()
        data = codecs.BOM_UTF8 + "café".encode()
        decoded = detector.decode(data)
        assert decoded.text == "café"


class TestUtf16NoBom:
    def test_ascii_heavy_utf16_le_detected(self):
        detector = EncodingDetector()
        data = "Hello world, this is plain ASCII text.".encode("utf-16-le")
        result = detector.detect(data)
        assert result.encoding == "utf-16-le"
        assert result.bom is False

    def test_decode_utf16_le_no_bom(self):
        detector = EncodingDetector()
        data = "hello there friend".encode("utf-16-le")
        decoded = detector.decode(data)
        assert decoded.text == "hello there friend"


class TestStrictUtf8:
    def test_plain_ascii_is_utf8(self):
        result = EncodingDetector().detect(b"hello world")
        assert result.encoding == "utf-8"
        assert not result.is_binary

    def test_multibyte_utf8_detected(self):
        result = EncodingDetector().detect("café naïve".encode())
        assert result.encoding == "utf-8"


class TestCp1252Fallback:
    def test_latin1_bytes_fall_back_to_cp1252(self):
        data = "Caf\xe9 na\xefve r\xe9sum\xe9".encode("cp1252")
        result = EncodingDetector().detect(data)
        assert result.encoding == "cp1252"
        assert any(w.startswith("encoding_fallback:") for w in result.warnings)

    def test_decode_cp1252(self):
        data = "Caf\xe9 na\xefve r\xe9sum\xe9 encore".encode("cp1252")
        decoded = EncodingDetector().decode(data)
        assert decoded.text == "Caf\xe9 na\xefve r\xe9sum\xe9 encore"


class TestBinaryDetection:
    def test_random_binary_is_binary_content(self):
        data = bytes([0, 1, 2, 3, 255, 254, 253, 252]) * 20
        result = EncodingDetector().detect(data)
        assert result.is_binary is True
        assert "binary_content" in result.warnings

    def test_executable_like_header_is_binary(self):
        data = b"\x7fELF" + bytes(range(200)) * 5
        result = EncodingDetector().detect(data)
        assert result.is_binary is True

    def test_empty_input_is_utf8_not_binary(self):
        result = EncodingDetector().detect(b"")
        assert result.encoding == "utf-8"
        assert result.is_binary is False


class TestNeverRaisesNeverNul:
    def test_decode_never_raises_on_garbage(self):
        garbage_samples = [
            bytes(range(256)),
            b"\xff\xfe\x00\x01\x02",
            b"\x00" * 100,
            b"normal text \x00 with embedded nul",
            bytes([i % 256 for i in range(5000)]),
        ]
        detector = EncodingDetector()
        for sample in garbage_samples:
            decoded = detector.decode(sample)
            assert "\x00" not in decoded.text

    def test_decode_of_truncated_multibyte_sequence_does_not_raise(self):
        # A UTF-8 sequence cut off mid-character.
        data = "café".encode()[:-1]
        decoded = EncodingDetector().decode(data)
        assert "\x00" not in decoded.text
