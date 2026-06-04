import pytest
from app.pipeline.transcriber import TranscriptSegment, MockTranscriber
from app.pipeline.silence import is_silent, classify_segments, reason_for_exclusion


class TestIsSilent:
    def test_empty_text_is_silent(self):
        seg = TranscriptSegment(start=0.0, end=10.0, text="", no_speech_prob=0.0)
        assert is_silent(seg) is True

    def test_whitespace_only_text_is_silent(self):
        seg = TranscriptSegment(start=0.0, end=10.0, text="   ", no_speech_prob=0.0)
        assert is_silent(seg) is True

    def test_high_no_speech_prob_is_silent(self):
        seg = TranscriptSegment(start=0.0, end=10.0, text="Some text.", no_speech_prob=0.6)
        assert is_silent(seg) is True

    def test_above_threshold_is_silent(self):
        seg = TranscriptSegment(start=0.0, end=10.0, text="Some text.", no_speech_prob=0.85)
        assert is_silent(seg) is True

    def test_combined_empty_and_high_prob_is_silent(self):
        seg = TranscriptSegment(start=30.0, end=45.0, text="", no_speech_prob=0.85)
        assert is_silent(seg) is True

    def test_non_empty_with_low_prob_is_active(self):
        seg = TranscriptSegment(start=0.0, end=15.0, text="Hello and welcome.", no_speech_prob=0.0)
        assert is_silent(seg) is False

    def test_just_below_threshold_is_active(self):
        seg = TranscriptSegment(start=0.0, end=10.0, text="Some text.", no_speech_prob=0.59)
        assert is_silent(seg) is False


class TestClassifySegments:
    def test_mock_transcriber_fixture(self):
        """MockTranscriber 5-segment fixture: 4 active, 1 silent."""
        transcriber = MockTranscriber()
        segments = transcriber.transcribe("dummy_path.wav")

        active, silent = classify_segments(segments)

        assert len(active) == 4
        assert len(silent) == 1

    def test_silent_segment_identity(self):
        """The single silent segment must be the empty-text one at 30-45s."""
        transcriber = MockTranscriber()
        segments = transcriber.transcribe("dummy_path.wav")

        _, silent = classify_segments(segments)

        assert silent[0].start == 30.0
        assert silent[0].end == 45.0
        assert silent[0].text == ""
        assert silent[0].no_speech_prob == 0.85

    def test_empty_input(self):
        active, silent = classify_segments([])
        assert active == []
        assert silent == []

    def test_all_active(self):
        segments = [
            TranscriptSegment(start=0.0, end=10.0, text="Hello.", no_speech_prob=0.0),
            TranscriptSegment(start=10.0, end=20.0, text="World.", no_speech_prob=0.1),
        ]
        active, silent = classify_segments(segments)
        assert len(active) == 2
        assert len(silent) == 0

    def test_all_silent(self):
        segments = [
            TranscriptSegment(start=0.0, end=10.0, text="", no_speech_prob=0.9),
            TranscriptSegment(start=10.0, end=20.0, text="", no_speech_prob=0.8),
        ]
        active, silent = classify_segments(segments)
        assert len(active) == 0
        assert len(silent) == 2


class TestReasonForExclusion:
    def test_locked_silent_segment_exact_string(self):
        """The locked silent segment must produce this exact reason string."""
        seg = TranscriptSegment(start=30.0, end=45.0, text="", no_speech_prob=0.85)
        result = reason_for_exclusion(seg)
        assert result == "silent (no_speech_prob=0.85); empty text"

    def test_only_high_prob_no_empty_text(self):
        seg = TranscriptSegment(start=0.0, end=10.0, text="Some text.", no_speech_prob=0.75)
        result = reason_for_exclusion(seg)
        assert result == "silent (no_speech_prob=0.75)"

    def test_only_empty_text_with_low_prob(self):
        seg = TranscriptSegment(start=0.0, end=10.0, text="", no_speech_prob=0.0)
        result = reason_for_exclusion(seg)
        assert result == "empty text"

    def test_active_segment_returns_empty_string(self):
        seg = TranscriptSegment(start=0.0, end=15.0, text="Hello.", no_speech_prob=0.0)
        result = reason_for_exclusion(seg)
        assert result == ""
