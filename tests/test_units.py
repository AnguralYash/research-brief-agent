"""Fast, no-network unit tests for the robustness-critical pure logic:
JSON extraction from messy model output, fetch-status classification, and
quote verification. Run with:  PYTHONPATH=src python3 -m pytest -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.fetch import _classify  # noqa: E402
from research_agent.llm import _parse_json  # noqa: E402
from research_agent.render import render_markdown  # noqa: E402
from research_agent.schema import Brief, FetchStatus, SourceDoc  # noqa: E402
from research_agent.stages import _norm  # noqa: E402


# ---- JSON parsing -------------------------------------------------------
def test_parse_plain_json():
    assert _parse_json('{"a": 1}') == {"a": 1}


def test_parse_fenced_json():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_with_prose_around():
    raw = 'Sure! Here you go:\n```\n{"claims": [1, 2]}\n```\nHope that helps.'
    assert _parse_json(raw) == {"claims": [1, 2]}


def test_parse_json_array():
    assert _parse_json("garbage [1, 2, 3] trailing") == [1, 2, 3]


def test_parse_json_invalid_raises():
    import pytest
    with pytest.raises(ValueError):
        _parse_json("not json at all")


# ---- fetch classification ----------------------------------------------
def test_classify_ok_when_enough_words():
    text = " ".join(["word"] * 200)
    assert _classify(text, "<html>...</html>") is FetchStatus.OK


def test_classify_paywall():
    assert _classify("Subscribe to continue reading.", "<html></html>") is FetchStatus.PAYWALL


def test_classify_js_required_when_scripty_and_empty():
    html = "<html>" + "<script src='x'></script>" * 6 + "</html>"
    assert _classify("", html) is FetchStatus.JS_REQUIRED


def test_classify_empty():
    assert _classify("", "<html><body></body></html>") is FetchStatus.EMPTY


# ---- quote verification normalization ----------------------------------
def test_norm_collapses_whitespace_and_case():
    assert _norm("  The  QUICK\n brown ") == "the quick brown"


def test_quote_substring_after_norm():
    source = _norm("The launch of ChatGPT reduced postings by 13% overall.")
    quote = _norm("reduced   POSTINGS by 13%")
    assert quote[:200] in source


# ---- off-topic source rendering ----------------------------------------
def _brief_with(sources):
    return Brief(topic="X", topic_inferred=False, generated_at="now",
                 provider="p", model="m", sources=sources)


def test_off_topic_source_renders_warning():
    bad = SourceDoc(id="S1", url="http://x", status=FetchStatus.OK, title="Wrong page",
                    word_count=500, on_topic=False, relevance_note="unrelated content")
    md = render_markdown(_brief_with([bad]))
    assert "Source relevance warning" in md
    assert "off-topic" in md
    assert "unrelated content" in md


def test_on_topic_source_has_no_warning():
    good = SourceDoc(id="S1", url="http://x", status=FetchStatus.OK, title="Right page",
                     word_count=500, on_topic=True)
    md = render_markdown(_brief_with([good]))
    assert "Source relevance warning" not in md
