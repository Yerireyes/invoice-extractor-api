import pytest

from app.invoices.adapters.fake import FAKE_MODEL, FakeExtractor
from app.shared.files import PageImage


def _page(content: bytes) -> PageImage:
    return PageImage(data=content, width=10, height=10)


@pytest.mark.anyio
async def test_same_input_gives_same_output() -> None:
    extractor = FakeExtractor()

    first = await extractor.extract([_page(b"factura-1")])
    second = await extractor.extract([_page(b"factura-1")])

    assert first == second
    assert first.model == FAKE_MODEL
    assert extractor.provider == "fake"


@pytest.mark.anyio
async def test_different_inputs_cover_all_samples() -> None:
    extractor = FakeExtractor()

    currencies = {
        (await extractor.extract([_page(f"archivo-{n}".encode())])).invoice.currency
        for n in range(40)
    }

    assert currencies == {"MXN", "EUR", "USD"}


@pytest.mark.anyio
async def test_confidence_values_are_within_range() -> None:
    output = await FakeExtractor().extract([_page(b"x")])

    assert all(0 <= value <= 1 for value in output.field_confidence.values())
