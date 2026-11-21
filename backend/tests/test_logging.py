import logging

from app.logsetup import RedactSecretsFilter


def test_redacts_configured_keys_and_groq_pattern() -> None:
    f = RedactSecretsFilter(["sk-secret-123456"])
    rec = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "using %s and %s", ("sk-secret-123456", "gsk_ABCdef123"), None
    )
    f.filter(rec)
    out = rec.getMessage()
    assert "sk-secret-123456" not in out and "gsk_ABCdef123" not in out


def test_untouched_record_keeps_args() -> None:
    f = RedactSecretsFilter(["sk-secret-123456"])
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "%s - %s", ("a", "b"), None)
    f.filter(rec)
    assert rec.args == ("a", "b")
