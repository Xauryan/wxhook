from backend.app.repository import _csv_cell


def test_csv_cell_neutralizes_spreadsheet_formulas() -> None:
    assert _csv_cell("=HYPERLINK(\"https://example.test\")") == "'=HYPERLINK(\"https://example.test\")"
    assert _csv_cell("  +1+1") == "'  +1+1"
    assert _csv_cell("ordinary text") == "ordinary text"
    assert _csv_cell(42) == 42
