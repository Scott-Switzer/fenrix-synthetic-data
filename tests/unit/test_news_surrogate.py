"""Tests for the NewsSurrogateGenerator (anonymization layer)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fenrix_synthetic.anonymization.news_surrogate_generator import (
    NewsSurrogateGenerator,
)


def _write_articles(tmp_path: Path, articles: list[dict]) -> Path:
    p = tmp_path / "originals" / "TESTCO" / "news"
    p.mkdir(parents=True, exist_ok=True)
    f = p / "articles.json"
    f.write_text(json.dumps(articles), encoding="utf-8")
    return f


SAMPLE_ARTICLES = [
    {
        "headline": "TESTCO announces Q3 earnings beat",
        "publisher": "Reuters",
        "published_timestamp": "2025-10-15T13:30:00Z",
        "summary": "TESTCO reported quarterly earnings that beat expectations.",
        "canonical_url": "https://reuters.com/testco-q3-2025",
        "source": "ticker_news",
        "body": "The CEO of TESTCO said revenue grew 12 percent.",
        "body_fetched": True,
        "fetch_status": "success",
        "related_tickers": ["TESTCO"],
    },
    {
        "headline": "TESTCO completes acquisition of Northbeam",
        "publisher": "Bloomberg",
        "published_timestamp": "2024-08-01T09:00:00Z",
        "summary": "TESTCO acquires Northbeam Industries in a $4B deal.",
        "canonical_url": "https://bloomberg.com/testco-northbeam",
        "source": "search_news",
        "body": "",
        "body_fetched": False,
        "fetch_status": "pending",
        "related_tickers": ["TESTCO", "NBIM"],
    },
]


class TestEventClassification:
    def test_earnings_classification(self) -> None:
        g = NewsSurrogateGenerator("NVDA")
        assert (
            g.classify_event_type("Reported quarterly earnings with strong EPS")
            == "earnings_release"
        )

    def test_merger_classification(self) -> None:
        g = NewsSurrogateGenerator("NVDA")
        assert (
            g.classify_event_type("Company completes acquisition of small biotech")
            == "merger_acquisition"
        )

    def test_executive_change_classification(self) -> None:
        g = NewsSurrogateGenerator("NVDA")
        text = "New CFO appointed"
        assert g.classify_event_type(text) == "executive_change"

    def test_default_general_corporate(self) -> None:
        g = NewsSurrogateGenerator("NVDA")
        assert g.classify_event_type("Miscellaneous update") == "general_corporate"


class TestDateGeneralization:
    def test_same_year_months_ago(self) -> None:
        g = NewsSurrogateGenerator("NVDA")
        # Use a fixed ref date so the test is deterministic
        ref = datetime(2026, 6, 20, tzinfo=UTC)
        result = g.generalize_date("2026-04-15T13:30:00Z", ref_date=ref)
        assert "2026" in result
        assert "2 months" in result or "ago" in result

    def test_prior_year(self) -> None:
        g = NewsSurrogateGenerator("NVDA")
        ref = datetime(2026, 6, 20, tzinfo=UTC)
        result = g.generalize_date("2024-08-01T09:00:00Z", ref_date=ref)
        assert "prior year" in result or "1 year" in result or "2024" in result

    def test_empty_timestamp(self) -> None:
        g = NewsSurrogateGenerator("NVDA")
        assert g.generalize_date("") == "[PERIOD DATE]"

    def test_unparseable_timestamp(self) -> None:
        g = NewsSurrogateGenerator("NVDA")
        assert g.generalize_date("not a date") == "[PERIOD DATE]"


class TestPublisherAndUrlRemoval:
    def test_publisher_removed(self) -> None:
        text = "According to Reuters, the company reported earnings."
        clean = NewsSurrogateGenerator.remove_publishers_and_urls(text)
        assert "[PUBLISHER REMOVED]" in clean
        assert "Reuters" not in clean

    def test_url_removed(self) -> None:
        text = "Read more at https://example.com/news/article-1 about this."
        clean = NewsSurrogateGenerator.remove_publishers_and_urls(text)
        assert "[URL REMOVED]" in clean
        assert "https://example.com" not in clean


class TestIdentityStripping:
    def test_company_replaced_when_in_graph(self) -> None:
        # Build a tiny graph builder substitute via dict
        from fenrix_synthetic.identity.fingerprint_graph import (
            FingerprintGraph,
        )
        from fenrix_synthetic.identity.schemas import EntityType

        g = FingerprintGraph("XX")
        g.add_entry(EntityType.COMPANY, "SecretCo International", "test")
        gen = NewsSurrogateGenerator("XX", fingerprint_graph=g)
        text = "SecretCo International reported earnings today."
        out, n = gen.strip_identity(text)
        assert "SecretCo International" not in out
        assert n >= 1

    def test_ticker_replaced_always(self) -> None:
        gen = NewsSurrogateGenerator("ZZZZ")
        text = "ZZZZ stock moved up 2 points."
        out, n = gen.strip_identity(text)
        # Without fingerprint graph, the company label is the default
        assert "Aster" in out or "[Entity-" in out
        assert "ZZZZ" not in out


class TestArticleGeneration:
    def test_label_present_in_surrogate(self) -> None:
        gen = NewsSurrogateGenerator("TESTCO")
        md, _aid, event, period = gen.generate_article(
            SAMPLE_ARTICLES[0], 0, ref_date=datetime(2026, 6, 20, tzinfo=UTC)
        )
        assert "synthetic financial news surrogate" in md.lower()
        assert "TESTCO" not in md  # ticker is replaced
        assert event == "earnings_release"
        assert "ago" in period or "period" in period or "prior" in period

    def test_identity_removed_in_surrogate(self) -> None:
        gen = NewsSurrogateGenerator("TESTCO")
        md, _aid, _event, _period = gen.generate_article(
            SAMPLE_ARTICLES[0], 0, ref_date=datetime(2026, 6, 20, tzinfo=UTC)
        )
        # Publisher removed
        assert "Reuters" not in md
        # URL removed
        assert "https://reuters.com" not in md

    def test_ticker_replaced_in_surrogate_text(self) -> None:
        # Verify the real ticker does NOT leak into public surrogate output.
        # (Headline text without the ticker is allowed to remain because it
        # carries no identity, only event-type signal.)
        ref = datetime(2026, 6, 20, tzinfo=UTC)
        gen = NewsSurrogateGenerator("TESTCO")
        md, _aid, event, _period = gen.generate_article(SAMPLE_ARTICLES[0], 0, ref_date=ref)
        assert "TESTCO" not in md
        assert "Aster" in md  # default synthetic company
        assert event == "earnings_release"
        # Synthetic label must be present
        assert "synthetic" in md.lower()
        # Synthetic label must be present
        assert "synthetic" in md.lower()


class TestPublicAndPrivateOutput:
    def test_public_files_written(self, tmp_path: Path) -> None:
        gen = NewsSurrogateGenerator("TESTCO")
        public_dir = tmp_path / "public" / "surrogates" / "news"
        private_dir = tmp_path / "private" / "TESTCO"
        result = gen.generate_from_articles(
            articles=SAMPLE_ARTICLES,
            public_dir=public_dir,
            private_dir=private_dir,
            ref_date=datetime(2026, 6, 20, tzinfo=UTC),
        )
        assert result.surrogates_generated == 2
        assert (public_dir).exists()
        files = list(public_dir.iterdir())
        assert len(files) == 2
        for f in files:
            content = f.read_text(encoding="utf-8")
            assert "synthetic financial news surrogate" in content.lower()
            assert "Reuters" not in content
            assert "Bloomberg" not in content
            assert "https://" not in content

    def test_private_provenance_no_raw_values(self, tmp_path: Path) -> None:
        gen = NewsSurrogateGenerator("TESTCO")
        public_dir = tmp_path / "public" / "surrogates" / "news"
        private_dir = tmp_path / "private" / "TESTCO"
        gen.generate_from_articles(
            articles=SAMPLE_ARTICLES,
            public_dir=public_dir,
            private_dir=private_dir,
            ref_date=datetime(2026, 6, 20, tzinfo=UTC),
        )
        prov_path = private_dir / "TESTCO_news_provenance.json"
        assert prov_path.exists()
        raw = prov_path.read_text(encoding="utf-8")
        # Hashes only — no raw publisher name or URL leaked
        assert "Reuters" not in raw
        assert "Bloomberg" not in raw
        assert "https://" not in raw
        # Provenance is JSON-loadable
        data = json.loads(raw)
        assert data["ticker"] == "TESTCO"
        assert data["surrogates_generated"] == 2
        assert data["articles_processed"] == 2
        assert isinstance(data["provenance_records"], list)
        for rec in data["provenance_records"]:
            assert "original_url_hash" in rec
            assert "original_publisher_hash" in rec
            assert "original_timestamp_hash" in rec
            assert "original_text_hash" in rec
            assert "event_type" in rec
            assert "relative_period" in rec


class TestEmptyAndErrorInputs:
    def test_empty_articles_returns_zero(self, tmp_path: Path) -> None:
        gen = NewsSurrogateGenerator("TESTCO")
        public_dir = tmp_path / "public" / "surrogates" / "news"
        private_dir = tmp_path / "private" / "TESTCO"
        result = gen.generate_from_articles(
            articles=[],
            public_dir=public_dir,
            private_dir=private_dir,
            ref_date=datetime(2026, 6, 20, tzinfo=UTC),
        )
        assert result.surrogates_generated == 0
        # Private provenance map is still written
        assert (private_dir / "TESTCO_news_provenance.json").exists()


class TestLimitRespected:
    def test_limit_news_caps_articles(self, tmp_path: Path) -> None:
        # Caller's responsibility to slice; verify the generator itself
        # generates one surrogate per call to generate_article().
        gen = NewsSurrogateGenerator("TESTCO")
        public_dir = tmp_path / "public" / "surrogates" / "news"
        private_dir = tmp_path / "private" / "TESTCO"
        result = gen.generate_from_articles(
            articles=SAMPLE_ARTICLES[:1],
            public_dir=public_dir,
            private_dir=private_dir,
            ref_date=datetime(2026, 6, 20, tzinfo=UTC),
        )
        assert result.surrogates_generated == 1
