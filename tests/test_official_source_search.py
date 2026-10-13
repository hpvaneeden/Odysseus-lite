import asyncio

from odysseus_extensions.official_source_search import official_source_plan
from src.agent_tools.web_tools import WebSearchTool


def test_official_usd_zar_prefers_sarb_when_both_authorities_are_named():
    plan = official_source_plan(
        "latest official USD/ZAR daily exchange rate Federal Reserve or SARB"
    )

    assert plan is not None
    assert plan.authority == "South African Reserve Bank"
    assert plan.search_query("latest official rate").startswith("site:resbank.co.za ")


def test_official_usd_zar_uses_fred_when_it_is_the_named_authority():
    plan = official_source_plan("latest official USD/ZAR exchange rate from FRED")

    assert plan is not None
    assert plan.authority == "Federal Reserve data"
    assert plan.host == "fred.stlouisfed.org"


def test_official_search_restricts_host_and_disables_day_filter(monkeypatch):
    captured = {}
    page = (
        "[CONTENT 1] From: https://www.resbank.co.za/current-market-rates\n"
        "Rand per US Dollar, 16.53, 2026-10-09."
    )

    def fake_search(query, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return page, [{
            "url": "https://www.resbank.co.za/current-market-rates",
            "title": "Current Market Rates",
        }]

    monkeypatch.setattr("src.search.comprehensive_web_search", fake_search)
    result = asyncio.run(WebSearchTool().execute(
        "latest official USD/ZAR daily exchange rate SARB Federal Reserve", {}
    ))

    assert result["exit_code"] == 0
    assert captured["query"].startswith("site:resbank.co.za ")
    assert captured["domain_whitelist"] == {"resbank.co.za", "www.resbank.co.za"}
    assert captured["time_filter"] is None
    assert "OFFICIAL SOURCE REQUIRED" in result["output"]
    assert "allratestoday.com" not in result["output"]


def test_official_search_fails_closed_without_fetched_first_party_content(monkeypatch):
    def fake_search(query, **kwargs):
        return (
            "[1] Current market rates\n"
            "Snippet: Rand per US Dollar, 16.53, 2026-10-09.",
            [{
                "url": "https://www.resbank.co.za/current-market-rates",
                "title": "Current Market Rates",
            }],
        )

    monkeypatch.setattr("src.search.comprehensive_web_search", fake_search)
    result = asyncio.run(WebSearchTool().execute(
        "latest official USD/ZAR daily exchange rate SARB", {}
    ))

    assert result["exit_code"] == 1
    assert "no fetched page content" in result["error"]
    assert "16.53" not in result["error"]


def test_nonofficial_latest_search_keeps_existing_day_filter(monkeypatch):
    captured = {}

    def fake_search(query, **kwargs):
        captured.update(kwargs)
        return "ordinary search output", []

    monkeypatch.setattr("src.search.comprehensive_web_search", fake_search)
    result = asyncio.run(WebSearchTool().execute("latest python release", {}))

    assert result["exit_code"] == 0
    assert captured["time_filter"] == "day"
    assert captured["domain_whitelist"] is None