from app.services.batches import CSV_COLUMNS, parse_csv, write_csv_template


def test_template_round_trips_as_valid(tmp_path):
    path = tmp_path / "template.csv"
    write_csv_template(str(path))

    rows = parse_csv(str(path))

    assert len(rows) == 1
    assert rows[0].is_valid
    assert rows[0].fields["to_city"] == "Boston"


def test_missing_required_column_raises(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("to_street1,to_city\n123 Main St,Boston\n", encoding="utf-8")

    try:
        parse_csv(str(path))
        assert False, "expected ValueError for missing required columns"
    except ValueError as exc:
        assert "missing required columns" in str(exc).lower()


def test_non_numeric_dimension_is_flagged(tmp_path):
    path = tmp_path / "rows.csv"
    header = ",".join(CSV_COLUMNS)
    row = "Jane,,123 Main St,,Boston,MA,02110,US,,,ten,6,4,16,"
    path.write_text(f"{header}\n{row}\n", encoding="utf-8")

    rows = parse_csv(str(path))

    assert len(rows) == 1
    assert not rows[0].is_valid
    assert any("length" in e for e in rows[0].errors)
