import pytest

from app.export.i18n import EXPORT_LANGUAGES, TEXTS, field_label, issue_message, text
from app.invoices.schemas import Issue, IssueCode


def test_all_languages_have_the_same_keys() -> None:
    keys = [set(TEXTS[language]) for language in EXPORT_LANGUAGES]

    assert all(language_keys == keys[0] for language_keys in keys)


@pytest.mark.parametrize("language", EXPORT_LANGUAGES)
def test_every_issue_code_has_a_message(language: str) -> None:
    for code in IssueCode:
        assert f"msg.{code.value}" in TEXTS[language]  # type: ignore[index]


@pytest.mark.parametrize(
    ("path", "es", "en"),
    [
        ("total", "Total", "Total"),
        ("issuer.name", "Nombre del emisor", "Issuer name"),
        ("line_items.0.amount", "Línea 1 · importe", "Line 1 · amount"),
        ("taxes.1.amount", "Impuesto 2 · importe", "Tax 2 · amount"),
        ("line_items.3", "Línea 4", "Line 4"),
        ("notes", "notes", "notes"),
        (None, "", ""),
    ],
)
def test_field_labels(path: str | None, es: str, en: str) -> None:
    assert field_label("es", path) == es
    assert field_label("en", path) == en


def test_positions_are_shown_one_based() -> None:
    issue = Issue(
        code=IssueCode.LINE_AMOUNT_MISMATCH,
        severity="warning",
        field="line_items.0.amount",
        params={"line": 0, "expected": "10.00", "actual": "9.00"},
    )

    assert issue_message("en", issue) == (
        "Line 1 amount does not add up: quantity × price − discount = 10.00, but it says 9.00."
    )


def test_missing_field_message_uses_field_label() -> None:
    issue = Issue(code=IssueCode.MISSING_REQUIRED_FIELD, severity="error", field="issuer.name")

    assert issue_message("es", issue) == "Falta un dato obligatorio: Nombre del emisor."


def test_low_confidence_is_shown_as_percentage() -> None:
    issue = Issue(
        code=IssueCode.LOW_CONFIDENCE, severity="info", field="total", params={"confidence": 0.62}
    )

    assert issue_message("es", issue) == "Dato leído con poca seguridad (62%): revísalo."


def test_text_formats_params() -> None:
    assert text("es", "pdf.page", page=1, pages=3) == "Página 1 de 3"
    assert text("en", "status.invalid") == "Invalid"
