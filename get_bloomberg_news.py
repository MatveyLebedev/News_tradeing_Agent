import requests
import feedparser
from datetime import datetime, timedelta
import json
from bs4 import BeautifulSoup
import time


def fetch_full_article_text(url, max_retries=5):
    """
    Attempt to fetch the full article text from the URL with retries
    Note: May not work for paywalled content
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
                    print(f"  ✓ Successfully fetched full text on attempt {attempt}")
                    return full_text
            
            # If we got here, content was not found
            if attempt == max_retries:
                return "Full text not available (possibly paywalled)"
                
        except requests.exceptions.HTTPError as e:
            if attempt == max_retries:
                return f"Error after {max_retries} attempts: {str(e)}"
            print(f"  ✗ HTTP Error on attempt {attempt}: {e.response.status_code}")
            
        except Exception as e:
            if attempt == max_retries:
                return f"Error after {max_retries} attempts: {str(e)}"
            print(f"  ✗ Error on attempt {attempt}: {str(e)}")
    
    return "Full text not available after all retries"


def get_bloomberg_news_rss(fetch_full_text=False):
    """
    Get Bloomberg technology news using RSS feed (no API key required)
    """
    print("Fetching Bloomberg Technology News via RSS...\n")
    
    # Bloomberg Technology RSS feed
    rss_url = "https://feeds.bloomberg.com/technology/news.rss"
    
    try:
        feed = feedparser.parse(rss_url)
        
        if not feed.entries:
            print("No articles found or feed unavailable.")
            return []
        
        articles = []
        for idx, entry in enumerate(feed.entries[:10], 1):  # Get top 10 articles
            article = {
                'title': entry.get('title', 'N/A'),
                'link': entry.get('link', 'N/A'),
                'published': entry.get('published', 'N/A'),
                'summary': entry.get('summary', 'N/A')
            }
            
            print(f"[{idx}/10] Title: {article['title']}")
            print(f"Published: {article['published']}")
            print(f"Link: {article['link']}")
            print(f"Summary: {article['summary'][:200]}...")
            
            if fetch_full_text:
                print(f"Fetching full article text (with up to 5 retries)...")
                full_text = fetch_full_article_text(article['link'], max_retries=1)
                article['full_text'] = full_text
                if full_text and not full_text.startswith("Error") and not full_text.startswith("Full text not available"):
                    print(f"Full text preview: {full_text[:300]}...")
                else:
                    print(f"Result: {full_text}")
                time.sleep(2)  # Be polite to the server between articles
            
            print("-" * 80)
            articles.append(article)
        
        return articles
        
    except Exception as e:
        print(f"Error fetching RSS feed: {e}")
        return []


def get_bloomberg_news_newsapi(api_key, from_date=None, to_date=None, query='technology', page_size=10):
    """
    Get Bloomberg technology news using NewsAPI with date filtering
    
    Args:
        api_key: NewsAPI key from newsapi.org
        from_date: Start date (datetime object or 'YYYY-MM-DD' string)
        to_date: End date (datetime object or 'YYYY-MM-DD' string)
        query: Search query (default: 'technology')
        page_size: Number of articles to fetch (max 100)
    """
    print("Fetching Bloomberg News via NewsAPI...\n")
    
    url = "https://newsapi.org/v2/everything"
    params = {
        'q': query,
        'sources': 'bloomberg',
        'sortBy': 'publishedAt',
        'apiKey': api_key,
        'pageSize': min(page_size, 100)  # NewsAPI max is 100
    }
    
    # Add date filters if provided
    if from_date:
        if isinstance(from_date, str):
            params['from'] = from_date
        else:
            params['from'] = from_date.strftime('%Y-%m-%d')
    
    if to_date:
        if isinstance(to_date, str):
            params['to'] = to_date
        else:
            params['to'] = to_date.strftime('%Y-%m-%d')
    
    print(f"Date range: {params.get('from', 'N/A')} to {params.get('to', 'N/A')}")
    print(f"Query: {query}\n")
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        if data['status'] != 'ok':
            print(f"Error: {data.get('message', 'Unknown error')}")
            return []
        
        if not data.get('articles'):
            print("No articles found for this time period.")
            return []
        
        print(f"Found {len(data['articles'])} articles\n")
        
        articles = []
        for idx, article in enumerate(data['articles'], 1):
            art = {
                'title': article.get('title', 'N/A'),
                'author': article.get('author', 'N/A'),
                'published': article.get('publishedAt', 'N/A'),
                'description': article.get('description', 'N/A'),
                'url': article.get('url', 'N/A'),
                'content': article.get('content', 'N/A')
            }
            articles.append(art)
            
            print(f"[{idx}/{len(data['articles'])}] Title: {art['title']}")
            print(f"Author: {art['author']}")
            print(f"Published: {art['published']}")
            print(f"Description: {art['description']}")
            print(f"URL: {art['url']}")
            print("-" * 80)
        
        return articles
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching news: {e}")
        return []


def save_to_json(articles, filename='bloomberg_tech_news.json'):
    """Save articles to JSON file"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)
    print(f"\nArticles saved to {filename}")


if __name__ == "__main__":
    print("=" * 80)
    print("BLOOMBERG TECHNOLOGY NEWS FETCHER")
    print("=" * 80)
    print()
    
    # Method 1: RSS Feed with full text scraping
    print("METHOD 1: RSS Feed with Full Text Scraping\n")
    print("Note: This will attempt to fetch full article text.")
    print("Some articles may be paywalled and return limited content.\n")
    
    articles = get_bloomberg_news_rss(fetch_full_text=True)
    
    if articles:
        save_to_json(articles, 'bloomberg_tech_news_full.json')
    
    print("\n" + "=" * 80)
    print("\nMETHOD 2: NewsAPI with Historical Data (Requires API Key)")
    print("To use NewsAPI for historical news:")
    print("1. Get free API key from https://newsapi.org/")
    print("2. Uncomment the code below and add your API key")
    print("3. Can fetch news from up to 1 month ago (free tier)")
    print("=" * 80)
    
    # Example: Get news from last 7 days
    # from datetime import timedelta
    # YOUR_API_KEY = "your_newsapi_key_here"
    # 
    # to_date = datetime.now()
    # from_date = to_date - timedelta(days=7)
    # 
    # articles_newsapi = get_bloomberg_news_newsapi(
    #     api_key=YOUR_API_KEY,
    #     from_date=from_date,
    #     to_date=to_date,
    #     query='technology',
    #     page_size=20
    # )
    # if articles_newsapi:
    #     save_to_json(articles_newsapi, 'bloomberg_tech_news_historical.json')
    
    print("\nFor more examples, see: get_historical_news.py")

