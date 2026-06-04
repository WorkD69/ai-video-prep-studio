import pytest
from app.pipeline.timecodes import to_global_time, format_hhmmss


class TestToGlobalTime:
    def test_zero_offset(self):
        assert to_global_time(30.0, 0) == 30.0

    def test_offset_600(self):
        assert to_global_time(15.0, 600) == 615.0

    def test_offset_3540(self):
        assert to_global_time(0.0, 3540) == 3540.0

    def test_both_zero(self):
        assert to_global_time(0.0, 0) == 0.0

    def test_fractional_seconds(self):
        assert to_global_time(1.5, 10.5) == 12.0


class TestFormatHhmmss:
    def test_zero(self):
        assert format_hhmmss(0.0) == "00:00:00"

    def test_615_seconds(self):
        assert format_hhmmss(615.0) == "00:10:15"

    def test_exactly_one_hour(self):
        assert format_hhmmss(3600.0) == "01:00:00"

    def test_3661_seconds(self):
        assert format_hhmmss(3661.0) == "01:01:01"

    def test_format_hhmmss_raises_on_negative(self):
        with pytest.raises(ValueError):
            format_hhmmss(-1.0)
