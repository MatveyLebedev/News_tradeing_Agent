"""
Graph tools for TradingAgent — 12 LangChain tools backed by Neo4j + pgvector.
All Cypher queries filter e.date <= timestamp (published_at of the article being analyzed).

TWO clocks, both enforced (2026-07-19 PIT audit):

  * `e.date` — when the event HAPPENED. Necessary but not sufficient: an article published
    today can describe a probe that started in May, and `e.date <= $pub` alone would let a
    May-dated decision read it. `_known_by()` adds the second gate — the event must be
    sourced from an article published by $pub. (NULL `published_at` passes: ~4% of edges
    predate the property, and the convention matches what search_events always did. On the
    live graph this gate removes ~half the events a 30-day ticker window returns.)
  * `Story.article_count` — a LIVE counter incremented as coverage accrues, so reading it
    tells a replayed decision how big the narrative eventually became. It is also lossy
    (story merges add 1 instead of absorbing the merged count, so it undercounts by an
    order of magnitude on big stories). Both readers now count in-date sourcing articles
    instead; see STORY_SATURATION_ARTICLES for the calibration consequence.
"""
import json
import os
import sys
from pathlib import Path

# Единая точка конфигурации моделей — корень репозитория на sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from model_config import EMBEDDING_MODEL

from langchain_core.tools import tool

# "Fully covered story" saturation point for the novelty score. Unchanged round prior,
# but the UNIT changed with the PIT fix (in-date sourcing articles instead of the drifting
# Story.article_count), so the old and new scores are not comparable and the constant is
# due a calibration pass — see docs/PIT_AUDIT_2026-07-19.md.
STORY_SATURATION_ARTICLES = 100.0


def _known_by(var: str = "e") -> str:
    """Cypher predicate: event `var` was reported by an article published on/before $pub.

    The knowledge-date gate that `e.date <= $pub` cannot provide (see module docstring).
    """
    return (f"EXISTS {{ MATCH ({var})-[sf:SOURCED_FROM]->(:Article) "
            f"WHERE sf.published_at IS NULL OR sf.published_at <= datetime($pub) }}")


def create_graph_tools(neo4j_manager, db_manager, timestamp):
    """
    Return a list of 12 LangChain tools bound to Neo4j + DB context.

    Args:
        neo4j_manager: Neo4jManager instance
        db_manager:    DB manager with vector_similarity_search(query_embedding, timestamp, limit)
        timestamp:     published_at datetime of the article being analyzed
    """
    pub_iso = timestamp.isoformat()

    # ── Original 5 tools ──────────────────────────────────────────────────────

    @tool
    def get_events_for_ticker(ticker: str, days: int = 30) -> str:
        """Get all events related to a stock ticker published before the current article,
        within the last N days. Includes cause/effect relationships."""
        rows = neo4j_manager.query("""
            MATCH (e:Event)-[r:AFFECTS]->(c:Company {ticker: $ticker})
            WHERE e.date <= datetime($pub)
              AND e.date >= datetime($pub) - duration({days: $days})
              AND """ + _known_by("e") + """
            OPTIONAL MATCH (e)-[cause:CAUSES|RELATED_TO]-(other:Event)
            WHERE other.date <= datetime($pub) AND """ + _known_by("other") + """
            RETURN e.id AS id, e.name AS name, e.type AS type,
                   e.description AS description, e.date AS date,
                   r.sentiment AS sentiment, r.confidence AS confidence,
                   CASE WHEN e.date + duration({minutes: 15})   <= datetime($pub) THEN r.move_15m ELSE null END AS move_15m,
                   CASE WHEN e.date + duration({minutes: 40})   <= datetime($pub) THEN r.move_40m ELSE null END AS move_40m,
                   CASE WHEN e.date + duration({minutes: 240})  <= datetime($pub) THEN r.move_4h  ELSE null END AS move_4h,
                   CASE WHEN e.date + duration({minutes: 1440}) <= datetime($pub) THEN r.move_1d  ELSE null END AS move_1d,
                   collect({rel: type(cause), event: other.name}) AS related
            ORDER BY e.date DESC
        """, {"ticker": ticker.upper(), "days": days, "pub": pub_iso})
        return json.dumps(rows, default=str)

    @tool
    def get_recent_events(limit: int = 20, event_type: str = None) -> str:
        """Get most recent events published before the current article,
        optionally filtered by type.
        Valid types: geopolitical_conflict, macro_economic, commodity_price,
        corporate_announcement, product_launch, earnings, merger_acquisition,
        trade_policy, regulatory, market_move, leadership_change, legal, other"""
        rows = neo4j_manager.query("""
            MATCH (e:Event)
            WHERE e.date <= datetime($pub)
              AND ($type IS NULL OR e.type = $type)
            RETURN e.id AS id, e.name AS name, e.type AS type,
                   e.description AS description, e.date AS date,
                   e.confidence AS confidence
            ORDER BY e.date DESC LIMIT $limit
        """, {"type": event_type, "limit": limit, "pub": pub_iso})
        return json.dumps(rows, default=str)

    @tool
    def find_related_events(event_id: str, depth: int = 2) -> str:
        """Traverse cause/effect chain from an event up to given depth.
        Only returns events published before the current article."""
        rows = neo4j_manager.query(f"""
            MATCH (start:Event {{id: $id}})
            WHERE start.date <= datetime($pub)
            MATCH path = (start)-[:CAUSES|RELATED_TO*1..{int(depth)}]-(related:Event)
            WHERE related.date <= datetime($pub)
            RETURN related.id AS id, related.name AS name,
                   related.type AS type, related.date AS date,
                   [r IN relationships(path) | {{type: type(r), evidence: r.evidence_text}}] AS chain
            ORDER BY related.date DESC
        """, {"id": event_id, "pub": pub_iso})
        return json.dumps(rows, default=str)

    @tool
    def search_events(query: str, limit: int = 10) -> str:
        """Semantic search for events published before the current article.
        Embeds the query, finds similar news in PostgreSQL via pgvector,
        then returns Neo4j events linked to those news articles."""
        from openai import OpenAI
        client = OpenAI(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
        )
        try:
            resp = client.embeddings.create(
                input=[query],
                model=EMBEDDING_MODEL,
                dimensions=1536,
            )
            query_vec = resp.data[0].embedding
        except Exception:
            return json.dumps([])

        similar_news = db_manager.vector_similarity_search(
            query_embedding=query_vec,
            timestamp=timestamp,
            limit=limit * 2,
        )
        news_ids = [n["id"] for n in similar_news]
        if not news_ids:
            return json.dumps([])

        rows = neo4j_manager.query("""
            MATCH (e:Event)-[r:SOURCED_FROM]->(a:Article)
            WHERE a.news_id IN $ids
              AND e.date <= datetime($pub)
              AND (r.published_at IS NULL OR r.published_at <= datetime($pub))
            RETURN DISTINCT e.id AS id, e.name AS name, e.type AS type,
                   e.description AS description, e.date AS date,
                   e.confidence AS confidence
            ORDER BY e.date DESC LIMIT $limit
        """, {"ids": news_ids, "limit": limit, "pub": pub_iso})
        return json.dumps(rows, default=str)

    @tool
    def get_story_context(ticker_or_domain: str) -> str:
        """Get the ongoing Story (narrative) for a ticker or macro domain,
        including event_count (events before current article) and all linked Events.
        Use this to understand how a situation has been evolving over time.
        Examples: ticker='NVDA', domain='geopolitics', domain='macro_markets'"""
        q = ticker_or_domain.upper() if len(ticker_or_domain) <= 5 else ticker_or_domain
        rows = neo4j_manager.query("""
            MATCH (s:Story)
            WHERE ($q IN s.tickers OR s.domain = $q)
              AND s.start_date >= datetime($pub) - duration({days: 30})
              AND s.start_date <= datetime($pub)
            MATCH (e:Event)-[:PART_OF]->(s)
            WHERE e.date <= datetime($pub)
              AND """ + _known_by("e") + """
            WITH s, collect({name: e.name, type: e.type, date: e.date}) AS events,
                 count(e) AS event_count
            ORDER BY event_count DESC LIMIT 3
            RETURN s.label AS label, s.domain AS domain,
                   event_count, s.start_date AS start_date, events
        """, {"q": q, "pub": pub_iso})
        return json.dumps(rows, default=str)

    # ── New tools (deep reasoning, macro/micro context) ───────────────────────

    @tool
    def get_event_price_impact(event_id: str) -> str:
        """Get actual price movements for all companies affected by a specific event.
        Returns move_15m, move_40m, move_4h, move_1d percentage changes from the
        AFFECTS edges in the graph. Use this AFTER finding an event via search_events
        or get_events_for_ticker to see its concrete market impact."""
        rows = neo4j_manager.query("""
            MATCH (e:Event {id: $id})-[r:AFFECTS]->(c:Company)
            WHERE e.date <= datetime($pub)
            RETURN e.name AS event_name, e.type AS event_type, e.date AS event_date,
                   c.ticker AS ticker, r.sentiment AS sentiment, r.confidence AS confidence,
                   CASE WHEN e.date + duration({minutes: 15})   <= datetime($pub) THEN r.move_15m ELSE null END AS move_15m,
                   CASE WHEN e.date + duration({minutes: 40})   <= datetime($pub) THEN r.move_40m ELSE null END AS move_40m,
                   CASE WHEN e.date + duration({minutes: 240})  <= datetime($pub) THEN r.move_4h  ELSE null END AS move_4h,
                   CASE WHEN e.date + duration({minutes: 1440}) <= datetime($pub) THEN r.move_1d  ELSE null END AS move_1d
        """, {"id": event_id, "pub": pub_iso})
        return json.dumps(rows, default=str)

    @tool
    def get_story_momentum(ticker_or_domain: str) -> str:
        """Get market attention momentum for Stories related to a ticker or macro domain.
        Returns article_count, articles_per_day rate, and event timeline density.
        High momentum = market is heavily focused on this narrative = news more likely priced in.
        Low momentum / new Story = information is novel = higher expected price impact.
        Examples: ticker='NVDA', domain='geopolitics', domain='macro_markets'"""
        q = ticker_or_domain.upper() if len(ticker_or_domain) <= 5 else ticker_or_domain
        rows = neo4j_manager.query("""
            MATCH (s:Story)
            WHERE ($q IN s.tickers OR s.domain = $q)
              AND coalesce(s.status, 'active') = 'active'
              AND s.start_date >= datetime($pub) - duration({days: 60})
              AND s.start_date <= datetime($pub)
            MATCH (e:Event)-[:PART_OF]->(s)
            WHERE e.date <= datetime($pub)
              AND """ + _known_by("e") + """
            OPTIONAL MATCH (e)-[sf:SOURCED_FROM]->(a:Article)
            WHERE sf.published_at IS NULL OR sf.published_at <= datetime($pub)
            WITH s, count(DISTINCT e) AS event_count,
                 count(DISTINCT a) AS article_count,
                 duration.between(s.start_date, datetime($pub)).days AS days_active
            RETURN s.label AS label, s.domain AS domain,
                   article_count, event_count, days_active,
                   CASE WHEN days_active > 0
                        THEN toFloat(article_count) / days_active
                        ELSE toFloat(article_count) END AS articles_per_day
            ORDER BY articles_per_day DESC LIMIT 5
        """, {"q": q, "pub": pub_iso})
        return json.dumps(rows, default=str)

    @tool
    def get_causal_chain_with_prices(event_id: str, depth: int = 2) -> str:
        """Traverse cause/effect chain from an event AND show price impacts on
        every affected company at each step. Use this to understand how an event
        propagates through the market — e.g. 'Iran tensions -> oil disruption ->
        energy stocks rally' with actual percentage moves at each link."""
        rows = neo4j_manager.query(f"""
            MATCH (start:Event {{id: $id}})
            WHERE start.date <= datetime($pub) AND {_known_by("start")}
            MATCH path = (start)-[:CAUSES|RELATED_TO*1..{int(depth)}]-(related:Event)
            WHERE related.date <= datetime($pub) AND {_known_by("related")}
            OPTIONAL MATCH (related)-[aff:AFFECTS]->(c:Company)
            WITH related, path,
                 collect(DISTINCT {{
                     ticker: c.ticker,
                     sentiment: aff.sentiment,
                     move_15m: CASE WHEN related.date + duration({{minutes: 15}})  <= datetime($pub) THEN aff.move_15m ELSE null END,
                     move_40m: CASE WHEN related.date + duration({{minutes: 40}})  <= datetime($pub) THEN aff.move_40m ELSE null END,
                     move_4h:  CASE WHEN related.date + duration({{minutes: 240}}) <= datetime($pub) THEN aff.move_4h  ELSE null END
                 }}) AS price_impacts
            RETURN related.id AS id, related.name AS name, related.type AS type,
                   related.date AS date, price_impacts,
                   [r IN relationships(path) | {{type: type(r), evidence: r.evidence_text}}] AS chain
            ORDER BY related.date DESC
        """, {"id": event_id, "pub": pub_iso})
        return json.dumps(rows, default=str)

    @tool
    def get_macro_snapshot(days: int = 7) -> str:
        """Get a structured snapshot of the macro market environment.
        Returns: (1) event type distribution showing what kinds of events dominate,
        (2) top macro Stories by article_count showing dominant narratives.
        Use this to understand the overall market regime before analyzing specific news."""
        type_dist = neo4j_manager.query("""
            MATCH (e:Event)
            WHERE e.date <= datetime($pub)
              AND e.date >= datetime($pub) - duration({days: $days})
            RETURN e.type AS event_type, count(e) AS count
            ORDER BY count DESC
        """, {"pub": pub_iso, "days": days})

        top_stories = neo4j_manager.query("""
            MATCH (s:Story)
            WHERE s.domain IN ['geopolitics', 'macro_markets', 'regulatory']
              AND coalesce(s.status, 'active') = 'active'
              AND s.start_date >= datetime($pub) - duration({days: $days})
              AND s.start_date <= datetime($pub)
            RETURN s.label AS label, s.domain AS domain,
                   s.article_count AS article_count
            ORDER BY s.article_count DESC LIMIT 10
        """, {"pub": pub_iso, "days": days})

        return json.dumps({
            "event_distribution": type_dist,
            "top_macro_stories": top_stories,
        }, default=str)

    @tool
    def get_novelty_score(search_query: str, ticker: str = None) -> str:
        """Assess how NOVEL this news is compared to what the market already knows.
        This is the most important signal for short-term trading: novel information
        moves prices, repetition of known narratives does not.
        Returns novelty_score (0-1), matching Stories with article_counts, and
        the closest existing event. Score interpretation:
        - 0.8-1.0: completely new information, expect full price impact
        - 0.5-0.8: new angle on known narrative, expect moderate impact
        - 0.2-0.5: continuation of well-covered story, expect small impact
        - 0.0-0.2: near-duplicate of existing coverage, expect minimal impact"""
        from openai import OpenAI
        client = OpenAI(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
        )
        try:
            resp = client.embeddings.create(
                input=[search_query],
                model=EMBEDDING_MODEL,
                dimensions=1536,
            )
            query_vec = resp.data[0].embedding
        except Exception:
            return json.dumps({"novelty_score": 0.5, "error": "embedding failed"})

        similar_news = db_manager.vector_similarity_search(
            query_embedding=query_vec,
            timestamp=timestamp,
            ticker=ticker,
            limit=5,
            min_time_diff_hours=1,
        )

        max_similarity = 0.0
        closest_news = None
        if similar_news:
            closest_news = similar_news[0]
            max_similarity = closest_news.get("similarity", 0.0)

        # Only use high-confidence article matches for story saturation lookup.
        # Low-similarity matches (spurious hits) would wrongly inherit story
        # article_counts from dominant megastories (e.g. Iran War with 500+ articles).
        STORY_SIM_THRESHOLD = 0.65
        story_rows = []
        max_article_count = 0
        high_sim_news = [n for n in similar_news[:5] if n.get("similarity", 0) >= STORY_SIM_THRESHOLD]
        if high_sim_news:
            news_ids = [n["id"] for n in high_sim_news]
            # Story saturation must be measured AS-OF: s.article_count is a live counter,
            # so it would tell a replayed decision how big the narrative eventually got.
            story_rows = neo4j_manager.query("""
                MATCH (seed:Event)-[:SOURCED_FROM]->(a0:Article)
                WHERE a0.news_id IN $ids AND seed.date <= datetime($pub)
                MATCH (seed)-[:PART_OF]->(s:Story)
                WHERE coalesce(s.status, 'active') = 'active'
                WITH DISTINCT s
                OPTIONAL MATCH (e:Event)-[:PART_OF]->(s)
                WHERE e.date <= datetime($pub)
                  AND """ + _known_by("e") + """
                OPTIONAL MATCH (e)-[sf:SOURCED_FROM]->(a:Article)
                WHERE sf.published_at IS NULL OR sf.published_at <= datetime($pub)
                RETURN s.label AS label, s.domain AS domain,
                       count(DISTINCT a) AS article_count
                ORDER BY article_count DESC LIMIT 3
            """, {"ids": news_ids, "pub": pub_iso})
            if story_rows:
                max_article_count = max(r.get("article_count", 0) or 0 for r in story_rows)

        # Saturation cap: STORY_SATURATION_ARTICLES = fully covered story.
        # The previous cap of 15 was too aggressive — it penalised even minor
        # coverage (15+ articles) the same as a 500-article megastory. NOTE: the input
        # is now the as-of article count (see the query above), a different and larger
        # unit than the old s.article_count — scores before/after the PIT fix are not
        # comparable and the cap is due a calibration pass.
        similarity_factor = max(0.0, 1.0 - max_similarity)
        article_factor = max(0.0, 1.0 - min(max_article_count / STORY_SATURATION_ARTICLES, 1.0))
        novelty_score = round(similarity_factor * 0.6 + article_factor * 0.4, 2)

        return json.dumps({
            "novelty_score": novelty_score,
            "max_similarity_to_existing": round(max_similarity, 3),
            "closest_existing_news": {
                "title": closest_news.get("title", ""),
                "similarity": round(closest_news.get("similarity", 0), 3),
            } if closest_news else None,
            "related_stories": [
                {"label": r["label"], "article_count": r["article_count"], "domain": r["domain"]}
                for r in story_rows
            ],
            "max_story_article_count": max_article_count,
            "interpretation": (
                "Novel information — expect full price impact" if novelty_score >= 0.8
                else "New angle on known narrative — expect moderate impact" if novelty_score >= 0.5
                else "Continuation of covered story — expect small impact" if novelty_score >= 0.2
                else "Near-duplicate coverage — expect minimal impact"
            ),
        }, default=str)

    @tool
    def get_cross_ticker_correlation(event_type: str, days: int = 30) -> str:
        """For a given event type, show which tickers were most affected historically
        and their average 40-minute price movements. Use this for macro events
        (geopolitical_conflict, macro_economic, commodity_price, trade_policy) to
        identify which companies react strongest and in which direction."""
        rows = neo4j_manager.query("""
            MATCH (e:Event {type: $type})-[r:AFFECTS]->(c:Company)
            WHERE e.date <= datetime($pub)
              AND e.date >= datetime($pub) - duration({days: $days})
              AND r.move_40m IS NOT NULL
              AND """ + _known_by("e") + """
            WITH c.ticker AS ticker, collect(r.move_40m) AS moves
            WHERE size(moves) >= 2
            RETURN ticker,
                   size(moves) AS event_count,
                   reduce(s = 0.0, x IN moves | s + x) / size(moves) AS avg_move_40m,
                   reduce(mn = 999.0, x IN moves | CASE WHEN x < mn THEN x ELSE mn END) AS min_move,
                   reduce(mx = -999.0, x IN moves | CASE WHEN x > mx THEN x ELSE mx END) AS max_move,
                   moves
            ORDER BY event_count DESC LIMIT 20
        """, {"type": event_type, "pub": pub_iso, "days": days})
        return json.dumps(rows, default=str)

    @tool
    def get_event_replay(event_id: str) -> str:
        """Replay a historical event as a full case study: shows the event details,
        all source articles with their publication timestamps, price impacts on
        affected companies, and related events within +-3 days.
        Use this to deeply understand a historical analogy before making predictions."""
        event_detail = neo4j_manager.query("""
            MATCH (e:Event {id: $id})
            WHERE e.date <= datetime($pub)
            RETURN e.id AS id, e.name AS name, e.type AS type,
                   e.description AS description, e.date AS date,
                   e.confidence AS confidence
        """, {"id": event_id, "pub": pub_iso})

        if not event_detail:
            return json.dumps({"error": "Event not found or after current timestamp"})

        articles = neo4j_manager.query("""
            MATCH (e:Event {id: $id})-[r:SOURCED_FROM]->(a:Article)
            WHERE e.date <= datetime($pub)
            RETURN a.news_id AS news_id, a.name AS title,
                   r.published_at AS published_at,
                   r.move_15m AS move_15m, r.move_40m AS move_40m
            ORDER BY r.published_at
        """, {"id": event_id, "pub": pub_iso})

        price_impacts = neo4j_manager.query("""
            MATCH (e:Event {id: $id})-[r:AFFECTS]->(c:Company)
            WHERE e.date <= datetime($pub)
            RETURN c.ticker AS ticker, r.sentiment AS sentiment,
                   r.move_15m AS move_15m, r.move_40m AS move_40m,
                   r.move_4h AS move_4h, r.move_1d AS move_1d
        """, {"id": event_id, "pub": pub_iso})

        nearby_events = neo4j_manager.query("""
            MATCH (e:Event {id: $id})
            WHERE e.date <= datetime($pub)
            WITH e.date AS ref_date
            MATCH (other:Event)
            WHERE other.id <> $id
              AND other.date <= datetime($pub)
              AND other.date >= ref_date - duration({days: 3})
              AND other.date <= ref_date + duration({days: 3})
            RETURN other.id AS id, other.name AS name, other.type AS type,
                   other.date AS date
            ORDER BY other.date LIMIT 15
        """, {"id": event_id, "pub": pub_iso})

        return json.dumps({
            "event": event_detail[0] if event_detail else None,
            "source_articles": articles,
            "price_impacts": price_impacts,
            "nearby_events_3day_window": nearby_events,
        }, default=str)

    return [
        get_events_for_ticker, get_recent_events, find_related_events,
        search_events, get_story_context,
        get_event_price_impact, get_story_momentum,
        get_causal_chain_with_prices, get_macro_snapshot,
        get_novelty_score, get_cross_ticker_correlation,
        get_event_replay,
    ]
