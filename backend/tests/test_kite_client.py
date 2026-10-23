import pytest

from app.kite_client import KiteScrapeError, _parse_value_to_mb


def test_parse_value_to_mb_handles_gb_with_decimal_comma():
    assert _parse_value_to_mb("4,138 GB") == pytest.approx(4237.312)


def test_parse_value_to_mb_handles_mb():
    assert _parse_value_to_mb("78,949 MB") == pytest.approx(78.949)


def test_parse_value_to_mb_handles_plain_integer_gb():
    assert _parse_value_to_mb("10 GB") == pytest.approx(10240.0)


def test_parse_value_to_mb_handles_kb():
    assert _parse_value_to_mb("512 KB") == pytest.approx(0.5)


def test_parse_value_to_mb_case_insensitive_unit():
    assert _parse_value_to_mb("2,5 gb") == pytest.approx(2560.0)


def test_parse_value_to_mb_raises_on_unparseable_text():
    with pytest.raises(KiteScrapeError):
        _parse_value_to_mb("N/A")
