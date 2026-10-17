import base64

import pytest
from fastapi import HTTPException

from app import support_attachments as sa

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
PDF = b"%PDF-1.7\n" + b"0" * 20


def upload(*files):
    return sa.AttachmentUpload(files=[sa.AttachmentFile(name=n, data=base64.b64encode(d).decode()) for n, d in files])


def test_signature_decides_type_not_name():
    out = sa.decode_files(upload(("rapor.exe", PNG), ("../../etc/x.html", PDF)))
    assert [o[1] for o in out] == ["image/png", "application/pdf"]
    assert [o[0] for o in out] == ["rapor.png", "x.pdf"]


def test_rejects_unknown_empty_and_large():
    for bad, code in ((b"<script>alert(1)</script>", 415), (b"", 422), (PNG + b"0" * sa.MAX_FILE_BYTES, 413)):
        with pytest.raises(HTTPException) as err:
            sa.decode_files(upload(("a.png", bad)) if bad else sa.AttachmentUpload(files=[sa.AttachmentFile(name="a", data="AAAA")]))
        assert err.value.status_code in (code, 422, 415)


def test_invalid_base64_is_rejected():
    with pytest.raises(HTTPException) as err:
        sa.decode_files(sa.AttachmentUpload(files=[sa.AttachmentFile(name="a.png", data="!!!!")]))
    assert err.value.status_code == 422


def test_at_most_three_files():
    with pytest.raises(Exception):
        upload(*[("a.png", PNG)] * 4)
