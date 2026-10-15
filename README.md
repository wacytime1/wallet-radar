<picture>
  <source media="(prefers-color-scheme: dark)" srcset="core/brand/coingecko-api-on-dark.svg">
  <img src="core/brand/coingecko-api-on-light.svg" alt="Data powered by CoinGecko API" height="32">
</picture>

# Wallet Radar

Find recurring profitable wallets behind active tokens.

**Active tokens → top traders → wallet analysis → my filters → shortlist**

Select Base (the default) or another supported network, choose Trending 1h / Trending 24h / New pools / Safe movers, and scan 5, 8, 10 or 15 tokens. The app deduplicates traders and recent pool participants, prioritizes recurring wallets, profiles ranked candidates in bounded batches, and applies your adjustable research filters. Click a shortlisted wallet to inspect its evidence without leaving the results.

## Install and run

Requires Python 3.12 or later. From this repository folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item env.example .env
```

Edit `.env` locally, in the same folder as `README.md` and `pyproject.toml`:

```dotenv
COINGECKO_API_KEY=<your key>
COINGECKO_ENVIRONMENT=pro
```

An Analyst plan or higher is required for the wallet research workflow. The key is read from `.env` on the backend only. `.env` is ignored by Git. Do not paste your key into chat, frontend code or commits. Restart the app after editing the file.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.server:app --port 8000
```

Open http://localhost:8000. Run tests offline with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

If `make` and `uv` are installed, the Makefile commands are available: `make install`, `make test`, `make run`. Windows users can use the direct Python commands above.

## Data and local research logic

CoinGecko API supplies pools, tokens, top traders, wallet PnL, swap history and supported wallet balances. This app computes recurrence, FIFO matching, win rates, activity summaries, infrastructure/bot heuristics, Research Score and shortlist decisions. These derived results are not official CoinGecko fields or classifications. No AI scoring or invented market data is used.

Realized PnL uses the API lifetime total where available, with the app's PnL sanity checks. If outliers are excluded, it can represent a cleaned sum from the returned token sample. If PnL is unavailable, the app may use FIFO matched lots in the returned wallet history. It does not claim 30-day PnL. Win rate uses profitable FIFO matched lots; with no matches it falls back to profitable sold tokens in the returned PnL sample. Trade count is buys plus sells in that token sample, or the returned wallet-trade count. The drawer explains the basis and limitations.

Research Score is deterministic: win rate contributes up to 50 points, positive realized PnL capped at $50,000 contributes up to 30, and sample size capped at 20 contributes up to 20. Sample size is the larger of FIFO matched lots and sold tokens. See `core/wallets.py` and `app/research.py`.

Default filters: PnL greater than $10,000, win rate at least 55%, at least 20 trades, latest observed activity within 30 days, at least 2 scanned tokens, and exclusion of likely bots/infrastructure. Recently active optionally limits activity to 3 days. Unknown required metrics cannot satisfy a numeric filter; unsupported optional sections do not invalidate otherwise useful evidence.

## Coverage, caching and API usage

The selector uses the configured wallet-capable networks and `WALLET_CHAIN_CAPS`; Base is the default, not the only network. Start with Base, Ethereum or BNB Chain for the full workflow. Solana supports PnL and trade history, but its balances are marked unavailable. Endpoint coverage and plan errors are shown per section. One failed profile does not stop the scan.

Discovery/results are cached in memory for 10 minutes by chain, source and token count. Wallet profiles retain the 30-minute cache. Changing filters or opening details makes no CoinGecko requests. **Refresh from API** bypasses both caches for the selected network. Cached results and profiles are lost on restart; saved scan details expire after 30 minutes. Scans are on demand, with up to three concurrent wallet profiles and one research scan at a time.

The client counts successful API requests; it does not know actual billed credits. The UI therefore reports successful requests and marks API credits unavailable, without estimating them. A small first scan helps conserve usage. API responses can be incomplete, and the returned pages are not a complete lifetime trade ledger.

The main screen focuses on wallet research; additional backend modules and secondary pages remain available. This tool does not execute trades. Research only; not financial advice, a trading recommendation or a performance guarantee.

## Before recording

After configuring `.env`, verify a small Base scan, filter changes, the wallet drawer and supported data sections. Real API data and visual behavior must be checked locally before claiming the app is ready for recording. The provided CoinGecko SVG is used unchanged in the header.

<!-- coingecko-links:start -->
- CoinGecko API: https://www.coingecko.com/en/api?utm_source=github&utm_content=wacy_time1
- Pricing: https://www.coingecko.com/en/api/pricing?utm_source=github&utm_content=wacy_time1
- Docs: https://docs.coingecko.com?utm_source=github&utm_content=wacy_time1
- Agent Skill + MCP: https://docs.coingecko.com/ai-integration?utm_source=github&utm_content=wacy_time1
<!-- coingecko-links:end -->


## Discovery pre-selection

Before full profiling, app-derived rules exclude exact known infrastructure matches, existing discovery bot-like flags, more than 5,000 observed buys/sells across scanned tokens, or more than 20 swaps in the sampled recent pool feeds. This reuses discovery data and makes no additional classification requests. These are research selection heuristics, not confirmed identities or official CoinGecko labels. Missing counts remain eligible.

Eligible candidates are ranked by token recurrence, observed recent activity, then discovery PnL. The app never fills unused slots with excluded candidates. The funnel retains the complete raw candidate count; pre-selection reasons and counts appear below the results, with per-address reasons included in the scan response. If none qualify, no wallet profiles are requested.

Maximum trades defaults to 5,000 and can be cleared to disable it. It applies to the returned profile's trade-count sample, not a guaranteed full lifetime count. This activity limit is independent of bot/infrastructure toggles. Existing cached scans can use the filter, but the improved candidate selection takes effect only on the next scan; it cannot replace wallets already profiled. Unknown machine-like addresses may still pass discovery checks and be excluded later by full-profile evidence.

## Iterative wallet profiling

Wallet Radar no longer treats the first 30 profiles as the final research pool. After discovery pre-selection, it profiles ranked candidates in batches of 30, gates each completed profile, and continues until a completed batch contains enough eligible profiles, the candidate list is exhausted, or the hard profile-attempt cap is reached.

Defaults in `app/config.py`: `TARGET_RESEARCHABLE_PROFILES = 50`, `MAX_FULL_PROFILES_PER_SCAN = 300`, `PROFILE_BATCH_SIZE = 30`. A completed batch may exceed the target of 50. Failed attempts also consume the safety cap; no excluded wallet is used to fill spare slots. Existing 30-minute wallet caching applies to every attempted profile, including those rejected by the gate.

The post-profile gate excludes known or heuristically identified infrastructure, bot-like activity, missing trade counts, profile failures, and trade counts above Maximum trades (default 5,000). The maximum is a research preference, not a bot classification. The app's count is based on the returned PnL token/trade sample and is not guaranteed to be a complete historical count. Clear Maximum trades to disable the activity limit for a new scan. The gate uses its value when the scan starts; subsequent filter edits only narrow the saved research pool and do not trigger additional profiling.

The funnel shows raw candidates, discovery-eligible candidates, wallet profile attempts, researchable profiles, and wallets passing user filters. Result responses preserve all processed profile rows and post-gate exclusion reasons for diagnosis. API usage remains visible as successful request counts rather than estimated billed credits. On a cached scan, the actual historical profile count is retained and new successful requests are zero.

## Strong research standards and deeper search

Default user filters are realized PnL greater than $10,000, win rate at least 55%, 20–5,000 trades in the available sample, activity within 30 days, and recurrence in at least two scanned tokens. Bot and infrastructure exclusions are on. Recently active is optional and off by default. No threshold is ever relaxed automatically; zero strong candidates is a valid result.

The ranked search continues in batches of 30 until at least 50 researchable profiles exist after a completed batch, 300 profile attempts have been processed, or the candidate universe is exhausted. The target can be exceeded within the final batch. The quality gate is unchanged, and excluded profiles cannot fill the target. Each address is processed at most once per scan; cached profiles count as processed attempts but generate no additional CoinGecko calls. Wallet profile cache TTL remains 30 minutes, and explicit Refresh from API continues to bypass it.

The funnel's wallets profiled value is the actual number of processed attempts, including cached and failed attempts. Successful profiles are separately retained as `successful_profile_count` in the result; `researchable_count` includes only post-gate survivors. The three internal depth values live in `app/config.py`; no new UI controls are required.

With 300 uncached EVM profiles, the core flow may make up to 900 successful profile requests (PnL, trades, balances), plus discovery requests. Unsupported sections, caching and early stopping reduce this. Retries can add request attempts; successful-request accounting is not a billed-credit estimate. A larger search can take substantially longer and can encounter API rate limits; concurrency remains three profiles at a time.
