"""Regression tests for #47456: browser subprocess output must decode
resiliently on non-UTF-8 system locales (GBK/Shift-JIS/EUC-KR) instead of
raising UnicodeDecodeError."""

import pytest

import tools.browser_tool as bt


# Bytes that are INVALID UTF-8 but valid in common Windows system locales.
#   0xb2 0xbb  -> GBK    (the exact lead byte 0xb2 from the issue's traceback)
#   0x82 0xa0  -> Shift-JIS (Japanese)
#   0xb0 0xa1  -> EUC-KR  (Korean)
_NON_UTF8 = [
    pytest.param(b"\xb2\xbb", id="gbk"),
    pytest.param(b"\x82\xa0", id="shift_jis"),
    pytest.param(b"\xb0\xa1", id="euc_kr"),
]


@pytest.mark.parametrize("raw", _NON_UTF8)
def test_read_subprocess_text_never_raises_on_non_utf8(tmp_path, raw):
    p = tmp_path / "out.txt"
    p.write_bytes(b'{"ok": true} ' + raw)
    # Must not raise (the bug raised UnicodeDecodeError here).
    out = bt._read_subprocess_text(str(p))
    assert '{"ok": true}' in out


def test_read_subprocess_text_roundtrips_bytes_losslessly(tmp_path):
    # surrogateescape preserves the original bytes (reversible), unlike replace.
    p = tmp_path / "out.txt"
    raw = b"hello \xb2\xbb world"
    p.write_bytes(raw)
    out = bt._read_subprocess_text(str(p))
    assert out.encode("utf-8", errors="surrogateescape") == raw


def test_read_subprocess_text_plain_utf8_unaffected(tmp_path):
    p = tmp_path / "out.txt"
    p.write_text("正常 UTF-8 텍스트 ✓", encoding="utf-8")
    out = bt._read_subprocess_text(str(p))
    assert out == "正常 UTF-8 텍스트 ✓"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"line1\r\nline2\r\n", id="crlf"),
        pytest.param(b"line1\rline2\r", id="lone_cr"),
        pytest.param(b"a\r\nb \xb2\xbb\rc", id="mixed_crlf_cr_non_utf8"),
    ],
)
def test_read_subprocess_text_preserves_newline_bytes(tmp_path, raw):
    # Text mode defaults to universal-newline translation (newline=None), which
    # rewrites \r\n and lone \r to \n and would break the lossless round-trip
    # the docstring promises (the Windows non-UTF-8 case this targets). The read
    # must preserve the raw CR/CRLF bytes verbatim.
    p = tmp_path / "out.txt"
    p.write_bytes(raw)
    out = bt._read_subprocess_text(str(p))
    assert out.encode("utf-8", errors="surrogateescape") == raw
