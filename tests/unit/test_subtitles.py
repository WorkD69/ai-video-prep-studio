"""Unit tests for app/pipeline/subtitles.py — WebVTT parsing (ADR 006)."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.pipeline.subtitles import parse_vtt  # noqa: E402

YOUTUBE_MANUAL = """WEBVTT
Kind: captions
Language: ru

NOTE this is a comment

00:00:00.200 --> 00:00:02.700 align:start position:0%
В этом видео мы с вами разберемся
с Claude Code.

00:00:02.800 --> 00:00:07.300
С его помощью можно &amp; нужно <c.colorE5E5E5>создать</c> сайт

1
01:02:03.000 --> 01:02:04,500
Hours and comma decimals
"""


def test_parses_cues_with_settings_and_multiline():
    segs = parse_vtt(YOUTUBE_MANUAL)
    assert [round(s.start, 3) for s in segs] == [0.2, 2.8, 3723.0]
    assert segs[0].end == 2.7
    assert segs[0].text == "В этом видео мы с вами разберемся с Claude Code."


def test_strips_tags_and_entities():
    assert parse_vtt(YOUTUBE_MANUAL)[1].text == "С его помощью можно & нужно создать сайт"


def test_cue_identifier_and_comma_decimal():
    seg = parse_vtt(YOUTUBE_MANUAL)[2]
    assert seg.text == "Hours and comma decimals"
    assert seg.end == 3724.5


def test_no_speech_prob_zero_for_subtitles():
    assert all(s.no_speech_prob == 0.0 for s in parse_vtt(YOUTUBE_MANUAL))


def test_merges_consecutive_duplicates():
    vtt = (
        "WEBVTT\n\n00:01.000 --> 00:02.000\nHi\n\n"
        "00:02.000 --> 00:03.000\nHi\n\n00:03.000 --> 00:04.000\nBye\n"
    )
    segs = parse_vtt(vtt)
    assert [(s.start, s.end, s.text) for s in segs] == [(1.0, 3.0, "Hi"), (3.0, 4.0, "Bye")]


def test_skips_empty_and_inverted_cues():
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\n<c></c>\n\n00:05.000 --> 00:04.000\nBad\n"
    assert parse_vtt(vtt) == []


def test_crlf_input():
    assert parse_vtt("WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000\r\nOk\r\n")[0].text == "Ok"
