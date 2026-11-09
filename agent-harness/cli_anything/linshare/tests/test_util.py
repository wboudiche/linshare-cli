from datetime import date

import pytest

from cli_anything.linshare.core.errors import UsageError
from cli_anything.linshare.core.util import (
    is_external,
    normalize_emails,
    parse_expires,
    safe_target_path,
)

TODAY = date(2026, 11, 6)


def test_parse_expires_relative():
    assert parse_expires("7d", TODAY) == date(2026, 11, 13)
    assert parse_expires("2w", TODAY) == date(2026, 11, 20)
    assert parse_expires("1m", TODAY) == date(2026, 12, 6)


def test_parse_expires_iso():
    assert parse_expires("2026-12-01", TODAY) == date(2026, 12, 1)


@pytest.mark.parametrize("value", ["2026-01-01", "soon", "0d", ""])
def test_parse_expires_rejects_past_and_garbage(value):
    with pytest.raises(UsageError):
        parse_expires(value, TODAY)


def test_normalize_emails_dedupes_case_insensitively():
    assert normalize_emails(["Marc@Acme.com", " marc@acme.com", "ann@x.org"]) == [
        "marc@acme.com",
        "ann@x.org",
    ]


def test_normalize_emails_rejects_invalid():
    with pytest.raises(UsageError) as exc:
        normalize_emails(["not-an-email"])
    assert "not-an-email" in exc.value.message


def test_normalize_emails_requires_at_least_one():
    with pytest.raises(UsageError):
        normalize_emails([])


def test_is_external():
    assert is_external("a@acme.com", "me@linagora.com") is True
    assert is_external("a@LINAGORA.com", "me@linagora.com") is False


def test_safe_target_path_no_overwrite(tmp_path):
    (tmp_path / "a.pdf").write_text("x")
    assert safe_target_path(tmp_path, "a.pdf") == tmp_path / "a (1).pdf"
    (tmp_path / "a (1).pdf").write_text("x")
    assert safe_target_path(tmp_path, "a.pdf") == tmp_path / "a (2).pdf"
    assert safe_target_path(tmp_path, "../evil.txt") == tmp_path / "evil.txt"


def test_error_to_dict_shape():
    err = UsageError("bad input")
    assert err.exit_code == 1
    assert err.to_dict() == {
        "error": {"code": "usage", "message": "bad input", "httpStatus": None, "linshareErrorCode": None}
    }


def test_iso_from_ms():
    from cli_anything.linshare.core.util import iso_from_ms

    assert iso_from_ms(1793750400000) == "2026-11-04T00:00:00Z"
    assert iso_from_ms(None) is None
    assert iso_from_ms("2026-11-04T10:00:00Z") == "2026-11-04T10:00:00Z"
