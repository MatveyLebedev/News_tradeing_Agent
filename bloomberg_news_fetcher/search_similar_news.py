#!/usr/bin/env python3
"""
Similarity search script for finding similar news articles using vector embeddings
"""
import psycopg2
import argparse
from typing import List, Tuple
from config import DB_CONFIG
from embeddings import EmbeddingGenerator
from tabulate import tabulate


def get_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=DB_CONFIG['host'],
        database=DB_CONFIG['database'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password'],
        port=DB_CONFIG['port']
    )


def search_similar_articles(query_text: str, top_k: int = 5, use_full_text: bool = False, dimensions: int = 1536) -> List[Tuple]:
    """
    Search for articles similar to the query text using pgvector HNSW index on 'embedding' column.

    Args:
        query_text: Text to search for
        top_k: Number of results to return
        use_full_text: Ignored (kept for backward compatibility). Always uses 'embedding' column.
        dimensions: Vector dimensions (must match stored vectors, default: 1536)

    Returns:
        List of (id, title, link, similarity_score) tuples
    """
    print(f"Generating {dimensions}-dimensional embedding for query...")
    generator = EmbeddingGenerator(output_dimensionality=dimensions)
    query_embedding = generator.generate_embedding(query_text)

    if not query_embedding:
        print("Failed to generate embedding for query")
        return []

    vec_str = '[' + ','.join(map(str, query_embedding)) + ']'

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT id, title, link, summary, source, published,
               1 - (embedding <=> '{vec_str}'::vector) AS similarity
        FROM news
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> '{vec_str}'::vector
        LIMIT %s
    """, (top_k,))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        print("No articles with embeddings found in database")
        return []

    print(f"Found {len(rows)} results via HNSW index.")
    return [(r[0], r[1], r[2], r[3], r[4], r[5], float(r[6])) for r in rows]


def main():
    parser = argparse.ArgumentParser(
        description='Search for similar news articles using vector embeddings'
    )
    parser.add_argument(
        'query',
        type=str,
        help='Query text to search for'
    )
    parser.add_argument(
        '--top',
        type=int,
        default=5,
        help='Number of results to return (default: 5)'
    )
    parser.add_argument(
        '--full-text',
        action='store_true',
        help='Use full text vectors instead of title+summary vectors'
    )
    parser.add_argument(
        '--dims',
        type=int,
        default=1536,
        help='Vector dimensions (must match stored vectors, default: 1536)'
    )
    
    args = parser.parse_args()
    
    print("=" * 100)
    print("SIMILARITY SEARCH FOR NEWS ARTICLES")
    print("=" * 100)
    print(f"\nQuery: {args.query}")
    print(f"Using: {'Full text vectors' if args.full_text else 'Title + Summary vectors'}")
    print(f"Dimensions: {args.dims}")
    print(f"Top {args.top} results")
    print()
    
    results = search_similar_articles(args.query, top_k=args.top, use_full_text=args.full_text, dimensions=args.dims)
    
    if not results:
        print("No results found")
        return
    
    print("=" * 100)
    print("RESULTS")
    print("=" * 100)
    print()
    
    for i, (article_id, title, link, summary, source, published, similarity) in enumerate(results, 1):
        print(f"[{i}] Similarity: {similarity:.4f}")
        print(f"    Title: {title}")
        print(f"    Source: {source}")
        print(f"    Published: {published}")
        print(f"    Link: {link}")
        print(f"    Summary: {summary[:200]}...")
        print()
    
    # Also show as table
    table_data = [
        [i, f"{sim:.4f}", title[:60] + "..." if len(title) > 60 else title, source]
        for i, (_, title, _, _, source, _, sim) in enumerate(results, 1)
    ]
    
    print("=" * 100)
    print("SUMMARY TABLE")
    print("=" * 100)
    print()
    print(tabulate(table_data, headers=['#', 'Similarity', 'Title', 'Source'], tablefmt='grid'))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nSearch cancelled")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

