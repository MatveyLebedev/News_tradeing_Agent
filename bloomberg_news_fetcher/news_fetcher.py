import requests
import feedparser
from datetime import datetime
import re
from bs4 import BeautifulSoup
import time
import psycopg2
import os
import pytz
from config import (
    DB_CONFIG,
    VERBOSE,
    BLOOMBERG_CATEGORIES,
    BLOOMBERG_RSS_BASE_URL,
    EXTERNAL_RSS_URLS,
    ENABLE_EXTERNAL_RSS,
)
from logger import DatabaseLogger
from embeddings import EmbeddingGenerator

# Initialize logger
logger = DatabaseLogger()

embedding_generator = EmbeddingGenerator()  # 1536 dims (gemini-embedding-001 via Open Router)

# US Eastern Time timezone (for consistency with trading agent and US market hours)
EASTERN_TZ = pytz.timezone('America/New_York')
# UTC timezone for RSS feeds (which typically provide dates in GMT/UTC)
UTC_TZ = pytz.UTC

RSS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


def fetch_full_article_text(url, max_retries=1, verbose=True):
    """
    Attempt to fetch the full article text from the URL with retries
    Note: May not work for paywalled content
    
    Args:
        url: Article URL to fetch
        max_retries: Number of retry attempts
        verbose: If False, suppresses console output
    """
    for attempt in range(1, max_retries + 1):
        try:
            # Rotate user agents to appear more like different browsers
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ]
            
            headers = {
                'User-Agent': user_agents[(attempt - 1) % len(user_agents)],
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            if attempt > 1:
                if verbose:
                    print(f"  Retry attempt {attempt}/{max_retries}...")
                time.sleep(2 * attempt)  # Increasing delay between retries
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Try to find article content (Bloomberg-specific selectors)
            article_body = None
            
            # Try different possible selectors for Bloomberg articles
            selectors = [
                'article',
                {'class': 'body-content'},
                {'class': 'article-body'},
                {'name': 'article'},
                {'class': 'fence-body'},
                {'class': 'article-content'}
            ]
            
            for selector in selectors:
                if isinstance(selector, dict):
                    article_body = soup.find(**selector)
                else:
                    article_body = soup.find(selector)
                if article_body:
                    break
            
            if article_body:
                # Get all paragraph text
                paragraphs = article_body.find_all('p')
                full_text = '\n\n'.join([p.get_text().strip() for p in paragraphs if p.get_text().strip()])
                if full_text:
                    if verbose:
                        print(f"  ✓ Successfully fetched full text on attempt {attempt}")
                    return full_text
            
            # If we got here, content was not found
            if attempt == max_retries:
                return "Full text not available (possibly paywalled)"
                
        except requests.exceptions.HTTPError as e:
            if attempt == max_retries:
                return f"Error after {max_retries} attempts: {str(e)}"
            if verbose:
                print(f"  ✗ HTTP Error on attempt {attempt}: {e.response.status_code}")
            
        except Exception as e:
            if attempt == max_retries:
                return f"Error after {max_retries} attempts: {str(e)}"
            if verbose:
                print(f"  ✗ Error on attempt {attempt}: {str(e)}")
    
    return "Full text not available after all retries"


def get_db_connection(verbose=True):
    """Create and return a database connection
    
    Args:
        verbose: If False, suppresses console output
    """
    try:
        conn = psycopg2.connect(
            host=DB_CONFIG['host'],
            database=DB_CONFIG['database'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            port=DB_CONFIG['port']
        )
        return conn
    except Exception as e:
        if verbose:
            print(f"Error connecting to database: {e}")
        raise


def article_exists(link, verbose=True):
    """
    Check if an article with the given link already exists in the database
    
    Args:
        link: Article URL to check
        verbose: If False, suppresses console output
        
    Returns:
        bool: True if article exists, False otherwise
    """
    try:
        conn = get_db_connection(verbose=verbose)
        cursor = conn.cursor()
        
        cursor.execute("SELECT 1 FROM news WHERE link = %s LIMIT 1", (link,))
        exists = cursor.fetchone() is not None
        
        cursor.close()
        conn.close()
        
        return exists
        
    except Exception as e:
        if verbose:
            print(f"Error checking if article exists: {e}")
        return False


def save_to_database(article, verbose=True):
    """
    Save a single article to the database with vector embeddings
    Checks if article exists first to avoid unnecessary vector generation
    
    Args:
        article: Article dictionary to save
        verbose: If False, suppresses console output
    """
    try:
        # Check if article already exists
        if article_exists(article['link'], verbose=verbose):
            if verbose:
                print("  ⚠ Article already exists in database, skipping")
            logger.info('DB_SKIP', f"Skipped existing article: {article['title']}", 
                       article_link=article['link'])
            return 'skipped'
        
        conn = get_db_connection(verbose=verbose)
        cursor = conn.cursor()
        
        # Generate embedding from title + summary (only vector stored)
        if verbose:
            print("  🔄 Generating embedding...")
        title_summary_vec = None
        try:
            title_summary_vec = embedding_generator.embed_title_summary(
                article.get('title', ''),
                article.get('summary', '')
            )
        except Exception as emb_err:
            logger.error('EMBEDDING_FAIL',
                         f"Embedding failed: {emb_err}",
                         article_link=article.get('link'),
                         details={'error': str(emb_err), 'title': article.get('title', '')[:100]})
        embedding_pgvector = ('[' + ','.join(map(str, title_summary_vec)) + ']') if title_summary_vec else None

        # Convert timezone-aware datetimes to naive (database stores as timestamp without time zone)
        # Convert from Eastern Time to naive datetime (database expects naive timestamps)
        published_dt_naive = article['published_dt'].replace(tzinfo=None) if article['published_dt'].tzinfo else article['published_dt']
        fetch_time_naive = article['fetch_time'].replace(tzinfo=None) if article['fetch_time'].tzinfo else article['fetch_time']

        # Insert article (no conflict expected since we checked)
        insert_query = """
            INSERT INTO news (published, fetch_time, title, link, summary, full_text, source,
                            embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT (link) DO NOTHING
            RETURNING id;
        """

        cursor.execute(insert_query, (
            published_dt_naive,
            fetch_time_naive,
            article['title'],
            article['link'],
            article['summary'],
            article['full_text'],
            article.get('source', 'bloomberg'),
            embedding_pgvector
        ))
        
        row = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        
        if row:
            news_id = row[0]
            logger.info('DB_SAVE', f"Saved new article: {article['title']}", 
                       article_link=article['link'],
                       details={'text_length': len(article.get('full_text', '')),
                               'has_embedding': bool(title_summary_vec)})
            return news_id
        else:
            # ON CONFLICT DO NOTHING resulted in no insert (race condition)
            if verbose:
                print("  ⚠ Article was inserted by another process")
            return 'skipped'
        
    except Exception as e:
        error_msg = f"Error saving article to database: {e}"
        if verbose:
            print(error_msg)
        logger.error('DB_ERROR', error_msg, 
                    article_link=article.get('link'),
                    details={'error': str(e), 'title': article.get('title')})
        return False


def parse_published_date(date_string):
    """
    Parse various date formats from RSS feeds and convert to Eastern Time
    RSS feeds typically provide dates in GMT/UTC, so we assume UTC if timezone is missing
    
    Args:
        date_string: Date string from RSS feed (typically in GMT/UTC)
        
    Returns:
        timezone-aware datetime in US Eastern Time
    """
    try:
        # Try parsing RFC 2822 format (common in RSS)
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_string)
        
        # If datetime is naive (no timezone), assume it's UTC (RSS feeds typically use GMT/UTC)
        if dt.tzinfo is None:
            dt = UTC_TZ.localize(dt)
        
        # Convert to Eastern Time
        return dt.astimezone(EASTERN_TZ)
    except:
        try:
            # Try ISO format
            dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
            
            # If datetime is naive, assume it's UTC
            if dt.tzinfo is None:
                dt = UTC_TZ.localize(dt)
            
            # Convert to Eastern Time
            return dt.astimezone(EASTERN_TZ)
        except:
            # Return current Eastern Time if parsing fails
            return datetime.now(EASTERN_TZ)


def normalize_text_for_compare(text):
    """
    Normalize text for equality checks (whitespace collapse).
    Used to detect cases where "full_text" accidentally becomes the same as "summary".
    """
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def extract_entry_text(entry):
    """
    Extract human-readable text from RSS entry content.
    Prefers content:encoded, then summary, then description (e.g. Thomson Reuters IR feed).
    """
    html_content = ""
    if entry.get('content') and len(entry['content']) > 0:
        html_content = entry['content'][0].get('value', '')
    if not html_content:
        html_content = entry.get('summary', '')
    if not html_content:
        html_content = entry.get('description', '')
    if not html_content:
        return ""
    return BeautifulSoup(html_content, 'html.parser').get_text(" ", strip=True)


def fetch_reuters_rss_feed(rss_url, source_name, verbose=True):
    """
    Fetch Reuters RSS feed with explicit User-Agent.
    Logs FETCH_403 when server returns 403.
    """
    headers = {
        "User-Agent": RSS_USER_AGENT,
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }
    try:
        response = requests.get(rss_url, headers=headers, timeout=15)
        if response.status_code == 403:
            msg = f"403 Forbidden while fetching {source_name} RSS"
            logger.error(
                "FETCH_403",
                msg,
                details={"feed_url": rss_url, "source": source_name, "status_code": 403},
            )
            if verbose:
                print(f"  ✗ {msg}: {rss_url}")
            return None

        response.raise_for_status()
        return feedparser.parse(response.content)
    except Exception as e:
        logger.error(
            "FETCH_ERROR",
            f"Error fetching {source_name} RSS feed",
            details={"feed_url": rss_url, "source": source_name, "error": str(e)},
        )
        if verbose:
            print(f"  ✗ Error fetching {source_name} RSS feed: {e}")
        return None


def fetch_and_save_news_from_category(category, fetch_full_text=True, verbose=True, articles_per_category=10):
    """
    Fetch Bloomberg news from a specific category via RSS and save to database
    All timestamps stored in US Eastern Time
    
    Args:
        category: News category (e.g., 'technology', 'economics', 'markets')
        fetch_full_text: If True, fetches full article text
        verbose: If False, suppresses console output
        articles_per_category: Number of articles to fetch per category (default: 10)
        
    Returns:
        Dictionary with saved_count, skipped_count, error_count
    """
    rss_url = f"{BLOOMBERG_RSS_BASE_URL}/{category}/news.rss"
    
    if verbose:
        print(f"Fetching Bloomberg {category.upper()} News via RSS...")
        print(f"URL: {rss_url}\n")
    
    logger.info('FETCH_START', f'Starting news fetch from {rss_url}', 
               details={'category': category, 'fetch_full_text': fetch_full_text, 'timezone': 'America/New_York'})
    
    try:
        try:
            resp = requests.get(rss_url, headers={"User-Agent": RSS_USER_AGENT}, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as req_err:
            logger.error('FETCH_ERROR', f'RSS request failed for {category}: {req_err}',
                        details={'category': category, 'feed_url': rss_url, 'error': str(req_err)})
            return {'saved_count': 0, 'skipped_count': 0, 'error_count': 1}

        if not feed.entries:
            msg = f"No articles found in {category} feed or feed unavailable."
            if verbose:
                print(f"  ⚠ {msg}")
            logger.warning('FETCH_EMPTY', msg, details={'feed_url': rss_url, 'category': category})
            return {'saved_count': 0, 'skipped_count': 0, 'error_count': 0}
        
        # Use Eastern Time for fetch_time (consistent with trading agent)
        fetch_time = datetime.now(EASTERN_TZ)
        saved_count = 0
        error_count = 0
        skipped_count = 0
        
        entries_to_process = feed.entries[:articles_per_category]
        
        for idx, entry in enumerate(entries_to_process, 1):
            # Parse and convert published date to Eastern Time
            published_dt = parse_published_date(entry.get('published', ''))
            
            article = {
                'title': entry.get('title', 'N/A'),
                'link': entry.get('link', 'N/A'),
                'published': entry.get('published', 'N/A'),
                'published_dt': published_dt,  # Already in Eastern Time
                'summary': entry.get('summary', 'N/A'),
                'fetch_time': fetch_time,  # Already in Eastern Time
                'full_text': '',
                'source': f'bloomberg-{category}'  # Store category in source field
            }
            
            if verbose:
                print(f"  [{idx}/{len(entries_to_process)}] {article['title']}")
                print(f"  Published: {published_dt.strftime('%Y-%m-%d %H:%M:%S %Z')} (ET)")
            
            if fetch_full_text:
                if verbose:
                    print("  Fetching full article text...")
                raw_full_text = fetch_full_article_text(article['link'], max_retries=1, verbose=False)

                # If we can't fetch full text, store empty string (so downstream logic can rely on that).
                if raw_full_text.startswith("Error") or raw_full_text.startswith("Full text not available"):
                    full_text = ""
                else:
                    full_text = raw_full_text

                # Guard: if scraper returned only summary-like content, don't store it as full_text.
                if full_text and normalize_text_for_compare(full_text) == normalize_text_for_compare(article.get("summary", "")):
                    full_text = ""

                article['full_text'] = full_text

                if full_text:
                    if verbose:
                        print(f"  ✓ Full text fetched ({len(full_text)} chars)")
                    logger.info(
                        'ARTICLE_FETCHED',
                        f"Fetched full text for: {article['title']}",
                        article_link=article['link'],
                        details={'text_length': len(full_text), 'category': category},
                    )
                else:
                    if verbose:
                        print("  ⚠ Full text unavailable")
                    logger.warning(
                        'ARTICLE_PARTIAL',
                        f"Partial/no full text for: {article['title']}",
                        article_link=article['link'],
                        details={'result': raw_full_text[:100], 'category': category},
                    )

                time.sleep(1)  # Be polite to the server between articles
            
            # Save to database
            result = save_to_database(article, verbose=False)
            if result == 'skipped':
                skipped_count += 1
                if verbose:
                    print("  ⚠ Already exists, skipped")
                continue
            elif isinstance(result, int):
                saved_count += 1
                if verbose:
                    print("  ✓ Saved to database")
                try:
                    from graph_db.event_updater import process_article_events
                    process_article_events(article, result)
                except Exception as graph_err:
                    logger.warning('GRAPH_FAIL', f"Event extraction failed: {graph_err}")
            else:
                error_count += 1
                if verbose:
                    print("  ✗ Failed to save")
            
            if verbose:
                print()
        
        if verbose:
            print(f"  ✓ {category.upper()}: {saved_count} saved, {skipped_count} skipped, {error_count} errors")
        logger.info('FETCH_COMPLETE', f'Fetch complete for {category}: {saved_count} saved, {skipped_count} skipped, {error_count} errors',
                   details={'category': category, 'saved_count': saved_count, 'skipped_count': skipped_count, 
                           'error_count': error_count, 'total_articles': len(entries_to_process)})
        return {'saved_count': saved_count, 'skipped_count': skipped_count, 'error_count': error_count}
        
    except Exception as e:
        error_msg = f"Error fetching {category} RSS feed: {e}"
        if verbose:
            print(f"  ✗ {error_msg}")
        logger.error('FETCH_ERROR', error_msg, details={'error': str(e), 'feed_url': rss_url, 'category': category})
        return {'saved_count': 0, 'skipped_count': 0, 'error_count': 1}


def fetch_and_save_news(fetch_full_text=True, verbose=True, categories=None, articles_per_category=10):
    """
    Fetch Bloomberg news from multiple categories via RSS and save to database
    All timestamps stored in US Eastern Time
    
    Args:
        fetch_full_text: If True, fetches full article text
        verbose: If False, suppresses console output
        categories: List of categories to fetch (default: all from config)
        articles_per_category: Number of articles to fetch per category (default: 10)
        
    Returns:
        Total number of articles saved
    """
    if categories is None:
        categories = BLOOMBERG_CATEGORIES
    
    if verbose:
        print("=" * 80)
        print("FETCHING BLOOMBERG NEWS FROM MULTIPLE CATEGORIES")
        print("=" * 80)
        print(f"Categories: {', '.join(categories)}")
        print(f"Articles per category: {articles_per_category}")
        print("⏰ Using timezone: US Eastern Time (America/New_York)\n")
    
    total_saved = 0
    total_skipped = 0
    total_errors = 0
    
    for category in categories:
        if verbose:
            print(f"\n{'=' * 80}")
            print(f"CATEGORY: {category.upper()}")
            print(f"{'=' * 80}")
        
        result = fetch_and_save_news_from_category(
            category=category,
            fetch_full_text=fetch_full_text,
            verbose=verbose,
            articles_per_category=articles_per_category
        )
        
        total_saved += result['saved_count']
        total_skipped += result['skipped_count']
        total_errors += result['error_count']

    if ENABLE_EXTERNAL_RSS:
        if verbose:
            print(f"\n{'=' * 80}")
            print("SOURCE: EXTERNAL RSS (CNBC + MarketWatch)")
            print(f"{'=' * 80}")

        for rss_item in EXTERNAL_RSS_URLS:
            # Allow both legacy `str` entries and new `{source, rss_url}` entries.
            if isinstance(rss_item, dict):
                rss_url = rss_item.get("rss_url")
                source_name = rss_item.get("source") or "external_rss"
            else:
                rss_url = rss_item
                from urllib.parse import urlparse
                netloc = urlparse(rss_url).netloc
                parts = netloc.split(".")
                source_name = parts[-2] if len(parts) >= 2 else parts[0]
            logger.info(
                "FETCH_START",
                f"Starting {source_name} RSS fetch from {rss_url}",
                details={"source": source_name, "feed_url": rss_url, "timezone": "America/New_York"},
            )
            feed = fetch_reuters_rss_feed(rss_url, source_name, verbose=verbose)
            if feed is None:
                total_errors += 1
                continue

            if not feed.entries:
                logger.warning(
                    "FETCH_EMPTY",
                    f"No entries found in {source_name} RSS feed",
                    details={"source": source_name, "feed_url": rss_url},
                )
                if verbose:
                    print(f"  ⚠ No entries found in {rss_url}")
                continue

            fetch_time = datetime.now(EASTERN_TZ)
            saved_count = 0
            skipped_count = 0
            error_count = 0
            entries_to_process = feed.entries[:articles_per_category]

            for entry in entries_to_process:
                published_raw = entry.get('published', entry.get('updated', ''))
                published_dt = parse_published_date(published_raw)
                summary_or_desc = entry.get('summary', entry.get('description', ''))
                summary_text = BeautifulSoup(summary_or_desc or '', 'html.parser').get_text(" ", strip=True)
                link = entry.get('link', 'N/A')
                if fetch_full_text:
                    full_text = fetch_full_article_text(link, max_retries=1, verbose=False)
                    if full_text.startswith("Error") or full_text.startswith("Full text not available"):
                        full_text = ""
                    # Guard: avoid storing summary as "full_text".
                    if full_text and normalize_text_for_compare(full_text) == normalize_text_for_compare(summary_text):
                        full_text = ""
                else:
                    full_text = ""

                article = {
                    'title': entry.get('title', 'N/A'),
                    'link': link,
                    'published': published_raw,
                    'published_dt': published_dt,
                    'summary': summary_text or 'N/A',
                    'fetch_time': fetch_time,
                    'full_text': full_text,
                    'source': source_name,
                }

                result = save_to_database(article, verbose=False)
                if result == 'skipped':
                    skipped_count += 1
                elif isinstance(result, int):
                    saved_count += 1
                    try:
                        from graph_db.event_updater import process_article_events
                        process_article_events(article, result)
                    except Exception as graph_err:
                        logger.warning('GRAPH_FAIL', f"Event extraction failed: {graph_err}")
                else:
                    error_count += 1

            total_saved += saved_count
            total_skipped += skipped_count
            total_errors += error_count

            logger.info(
                "FETCH_COMPLETE",
                f"Fetch complete for {source_name}: {saved_count} saved, {skipped_count} skipped, {error_count} errors",
                details={
                    "source": source_name,
                    "feed_url": rss_url,
                    "saved_count": saved_count,
                    "skipped_count": skipped_count,
                    "error_count": error_count,
                    "total_articles": len(entries_to_process),
                },
            )
            if verbose:
                print(
                    f"  ✓ {source_name.upper()}: {saved_count} saved, "
                    f"{skipped_count} skipped, {error_count} errors"
                )
    
    if verbose:
        print("\n" + "=" * 80)
        print("FETCH COMPLETE - SUMMARY")
        print("=" * 80)
        print(f"Total saved: {total_saved}")
        print(f"Total skipped: {total_skipped}")
        print(f"Total errors: {total_errors}")
        print("=" * 80)
    
    logger.info('FETCH_ALL_COMPLETE', 
               f'All categories fetch complete: {total_saved} saved, {total_skipped} skipped, {total_errors} errors',
               details={'categories': categories, 'saved_count': total_saved, 'skipped_count': total_skipped, 
                       'error_count': total_errors})
    
    return total_saved


if __name__ == "__main__":
    # Read verbose setting from config (can be overridden by environment variable)
    verbose = os.getenv("VERBOSE", str(VERBOSE)).lower()
    
    if verbose:
        print("=" * 80)
        print("BLOOMBERG TECHNOLOGY NEWS FETCHER")
        print("=" * 80)
        print()
    
    result = fetch_and_save_news(fetch_full_text=True, verbose=verbose)
    
    if verbose:
        print(f"\nFetched and saved {result} articles")

