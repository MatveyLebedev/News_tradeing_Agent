# Package marker. NOTE: modules in here use FLAT imports (`from config import ...`)
# because the news-fetcher container copies them into /app root and runs them as
# scripts (Dockerfile.news). Import them as a package from the repo root with care:
# flat deps then resolve to the ROOT shims (config.py) — fine for TOP_50_TICKERS-style
# constants, wrong for modules that import `logger` (no root logger.py). Full package
# conversion is deferred until the container switches to `-m` module execution.
