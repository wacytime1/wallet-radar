"""Bounded on-demand research, built on the starter's discovery and profile cache."""
import time
import secrets
import asyncio
from dataclasses import dataclass

from core.client import CoinGeckoClient, CoinGeckoError, PlanRestrictedError
from core.wallets import _f
from . import profile, scan, config


@dataclass
class Filters:
    min_pnl: float = 10000
    min_win_rate: float = .55
    min_trades: int = 20
    max_trades: int | None = 5000
    max_days: float = 30
    min_tokens: int = 2
    recently_active: bool = False
    exclude_bots: bool = True
    exclude_infra: bool = True


def rejection_reasons(row: dict, filters: Filters) -> list[str]:
    """Explain every failed rule, including unknown evidence, using submitted filters."""
    reasons = []
    for key, limit, strict in (("realized_pnl_usd", filters.min_pnl, True),
                               ("win_rate", filters.min_win_rate, False),
                               ("trades", filters.min_trades, False),
                               ("tokens_seen_in", filters.min_tokens, False)):
        value = row.get(key)
        if value is None:
            reasons.append(f"missing {key}")
        elif value <= limit if strict else value < limit:
            reasons.append(key)
    if filters.max_trades is not None and row.get("trades") is not None and row["trades"] > filters.max_trades:
        reasons.append("maximum trades")
    days = row.get("days_since_last_trade")
    if days is None:
        reasons.append("missing activity")
    elif days > filters.max_days:
        reasons.append("inactivity")
    if filters.recently_active and (days is None or days > 3):
        reasons.append("recent activity")
    if filters.exclude_bots and (row.get("likely_bot") or row.get("label") == "bot_like" or row.get("style") == "bot"):
        reasons.append("likely bots")
    if filters.exclude_infra and (row.get("known_infra") or row.get("is_infra")):
        reasons.append("infrastructure")
    if row.get("error"):
        reasons.append("profile error")
    return reasons


def passes(row: dict, filters: Filters) -> bool:
    return not rejection_reasons(row, filters)


def evidence(candidate: dict, summary: dict) -> dict:
    row = {**candidate, **summary}
    pnl = summary.get("pnl_features") or {}
    matched = summary.get("match_metrics") or {}
    available = pnl.get("available")
    row["realized_pnl_usd"] = pnl.get("lifetime_realized_pnl_usd") if available else matched.get("realized_pnl_usd") if matched.get("trades") else None
    row["pnl_basis"] = "API lifetime total / app sanity checks" if available else "FIFO matched lots in returned trade history"
    row["win_rate_basis"] = "FIFO matched lots" if matched.get("trades") else "Sold tokens in returned PnL sample"
    row["trades"] = pnl.get("total_buys", 0) + pnl.get("total_sells", 0) if available else len(summary.get("recent_trades") or []) if summary.get("status", {}).get("trades") == "ok" else None
    row["trades_basis"] = "Buys + sells in returned PnL token sample" if available else "Returned wallet trades"
    last = max(summary.get("last_seen_ts") or 0, candidate.get("recent_last_ts") or 0)
    row["last_seen_ts"] = last or None
    row["days_since_last_trade"] = max(0, (time.time() - last) / 86400) if last else None
    row["research_score"] = summary.get("skill_score") if available or matched.get("trades") else None
    return row


def safe_error(exc: Exception) -> str:
    if isinstance(exc, PlanRestrictedError):
        return "This feature requires CoinGecko Analyst API access."
    if isinstance(exc, CoinGeckoError) and exc.status == 429:
        return "CoinGecko API rate limit reached. Try again shortly."
    if isinstance(exc, CoinGeckoError) and exc.status in (400, 404, 422):
        return "This wallet data is not available on this network."
    return "CoinGecko data could not be loaded. Try again shortly."


# Discovery-only thresholds; sample counts are not a wallet's total lifetime activity.
DISCOVERY_MAX_TRADES = 5000
RECENT_MACHINE_THRESHOLD = 20


def select_candidates(candidates: list[dict], limit: int = 30) -> dict:
    """Never fill spare profile slots with candidates already flagged as machine-like.

    This is an app research heuristic, not an identity assertion or CoinGecko label.
    Missing counts are allowed; full profiles and final filters can supply more evidence.
    """
    eligible, excluded = [], []
    for candidate in candidates:
        reasons = []
        if candidate.get("known_infra"):
            reasons.append("known infrastructure address")
        if candidate.get("likely_bot"):
            reasons.append("discovery bot-like activity heuristic")
        if (candidate.get("trades_seen") or 0) > DISCOVERY_MAX_TRADES:
            reasons.append("more than 5000 observed buys/sells across scanned tokens")
        if (candidate.get("recent_trades_seen") or 0) > RECENT_MACHINE_THRESHOLD:
            reasons.append("more than 20 swaps in sampled recent pool feeds")
        if reasons:
            excluded.append({"address": candidate["address"], "reasons": reasons})
        else:
            eligible.append(candidate)
    eligible.sort(key=lambda c: (-c.get("tokens_seen_in", 0), not c.get("recently_active", False),
                                -c.get("realized_seen_usd", 0), c["address"]))
    counts = {}
    for candidate in excluded:
        for reason in candidate["reasons"]:
            counts[reason] = counts.get(reason, 0) + 1
    return {"selected": eligible[:limit], "eligible_count": len(eligible),
            "excluded_count": len(excluded), "excluded": excluded, "reasons": counts}


def quality_reasons(row, max_trades=5000):
    reasons = []
    if row.get("known_infra") or row.get("is_infra") or row.get("label") == "protocol":
        reasons.append("infrastructure")
    if row.get("likely_bot") or row.get("label") == "bot_like" or row.get("style") == "bot":
        reasons.append("likely bot")
    if row.get("error"):
        reasons.append("profile error")
    if row.get("trades") is None:
        reasons.append("trade count unavailable")
    elif max_trades is not None and row["trades"] > max_trades:
        reasons.append("maximum trades research preference")
    return reasons


def quality_debug(row, max_trades=5000):
    bot_reasons = list(row.get("discovery_bot_reasons") or [])
    if row.get("likely_bot") and not bot_reasons:
        bot_reasons.append("discovery likely_bot flag; per-token evidence not retained in this legacy profile")
    activity = row.get("activity") or {}
    rate = activity.get("recent_trades_per_day", row.get("recent_trades_per_day"))
    if row.get("label") == "bot_like":
        bot_reasons.append(f"profile label bot_like: sampled rate {rate} > 50 trades/day")
    if row.get("style") == "bot":
        gap = activity.get("median_seconds_between_trades")
        sample = activity.get("recent_trades")
        bot_reasons.append(f"style bot: rate={rate}/day >50 OR median gap={gap}s <=10 with sample={sample} >=20")
    infra_reasons = []
    if row.get("known_infra") or row.get("infra_name"):
        infra_reasons.append(f"exact known address match: {row.get('known_infra') or row.get('infra_name')}")
    if row.get("is_infra") or row.get("label") == "protocol":
        pnl = row.get("pnl_features") or {}
        if not (row.get("known_infra") or row.get("infra_name")):
            trades = (pnl.get("total_buys") or 0) + (pnl.get("total_sells") or 0)
            infra_reasons.append(f"profile protocol heuristic: sample buys+sells={trades}, networks={len(pnl.get('active_networks') or [])}, total_tokens={pnl.get('tokens_traded')}; rule trades>3000 and networks>=4 OR tokens>300 and networks>=3")
    reasons = quality_reasons(row, max_trades)
    return {"address": row["address"], "discovery_likely_bot": bool(row.get("likely_bot")), "likely_bot": bool(bot_reasons), "likely_bot_reasons": bot_reasons,
            "infrastructure": bool(infra_reasons), "infrastructure_reasons": infra_reasons,
            "trade_count": row.get("trades"), "trade_count_source": row.get("trades_basis", "fixture / legacy count; source not recorded"),
            "maximum_trades": max_trades, "excluded_by_quality_gate": reasons, "researchable": not reasons}


def normalize_pool(result):
    """Re-gate cached legacy results instead of interpreting all profile rows as eligible."""
    raw = result.get("profile_rows", result.get("rows", []))
    limit = result.get("quality_max_trades", 5000)
    diagnostics = [quality_debug(row, limit) for row in raw]
    rows = [{**row, "quality_debug": debug} for row, debug in zip(raw, diagnostics) if debug["researchable"]]
    return {**result, "pool_version": 2, "profile_rows": raw, "rows": rows,
            "researchable_count": len(rows), "quality_max_trades": limit,
            "quality_debug": diagnostics,
            "quality_excluded": [{"address": d["address"], "reasons": d["excluded_by_quality_gate"]} for d in diagnostics if not d["researchable"]]}


async def profile_batches(candidates, fetch, *, target=None, cap=None, batch_size=None, max_trades=5000):
    """A finished batch is gated before the next one starts. Failures consume safety slots."""
    target = config.TARGET_RESEARCHABLE_PROFILES if target is None else target
    cap = config.MAX_FULL_PROFILES_PER_SCAN if cap is None else cap
    batch_size = max(1, config.PROFILE_BATCH_SIZE if batch_size is None else batch_size)
    cap = max(0, min(cap, config.MAX_FULL_PROFILES_PER_SCAN))
    seen = set()
    unique = []
    for candidate in candidates:
        address = candidate["address"]
        key = address.lower() if address.startswith("0x") else address
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    candidates = unique
    all_rows, eligible, excluded = [], [], []
    processed = 0
    for offset in range(0, min(len(candidates), cap), max(1, batch_size)):
        batch = candidates[offset:min(offset + batch_size, cap)]
        semaphore = asyncio.Semaphore(3)
        async def one(candidate):
            async with semaphore:
                try:
                    return await fetch(candidate)
                except Exception as exc:
                    return {**candidate, "error": safe_error(exc)}
        tasks = [asyncio.create_task(one(c)) for c in batch]
        try:
            for task in asyncio.as_completed(tasks):
                row = await task
                processed += 1
                debug = quality_debug(row, max_trades)
                row = {**row, "quality_debug": debug}
                all_rows.append(row)
                reasons = debug["excluded_by_quality_gate"]
                if reasons:
                    excluded.append({"address": row["address"], "reasons": reasons})
                else:
                    eligible.append(row)
                yield "progress", {"message": f"Analysed {processed} wallets · {len(eligible)} researchable · target {target} · limit {cap}",
                                   "processed_count": processed, "researchable_count": len(eligible)}
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if len(eligible) >= target:
            break
    yield "pool", {"rows": eligible, "profile_rows": all_rows, "quality_excluded": excluded,
                   "profile_attempts": processed, "researchable_count": len(eligible),
                   "profiled_count": processed,
                   "successful_profile_count": sum(not row.get("error") and any(s == "ok" for s in row.get("status", {}).values()) for row in all_rows),
                   "stop_reason": "target reached" if len(eligible) >= target else "profile cap reached" if processed >= cap else "candidates exhausted"}


_cache: dict = {}
_runs: dict = {}


def get_run(run_id):
    entry = _runs.get(run_id)
    return normalize_pool(entry[1]) if entry and time.time() - entry[0] < 1800 else None


def current_rows(result):
    # Recompute inactivity when filters change, without another API call.
    rows = [{**r, "days_since_last_trade": max(0, (time.time() - r["last_seen_ts"]) / 86400)
             if r.get("last_seen_ts") else None} for r in normalize_pool(result)["rows"]]
    return rows


def shortlist(result, filters):
    return sorted((r for r in current_rows(result) if passes(r, filters)), key=lambda r: -(r.get("research_score") or 0))


async def stream(chain, source, count, refresh=False, max_wallets=None, max_trades=5000):
    max_wallets = min(max_wallets if max_wallets is not None else config.MAX_FULL_PROFILES_PER_SCAN, config.MAX_FULL_PROFILES_PER_SCAN)
    key = (chain, source, count, max_wallets, max_trades, config.TARGET_RESEARCHABLE_PROFILES, config.PROFILE_BATCH_SIZE)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 600 and not refresh:
        normalized = normalize_pool(hit[1])
        _runs[hit[1]["run_id"]] = (hit[0], normalized)
        yield "result", {**normalized, "cached_at": hit[0], "successful_requests": 0}
        return
    async with CoinGeckoClient() as client:
        if refresh:
            # Explicit refresh also bypasses the otherwise preserved 30-minute profile cache.
            for cache_key in list(profile._cache):
                if cache_key.startswith(f"{chain}:"):
                    profile._cache.pop(cache_key, None)
        yield "progress", {"message": "Finding active tokens…"}
        tokens = await scan._tokens_from_pools(client, chain, source, count)
        yield "tokens", tokens
        yield "progress", {"message": "Finding traders…"}
        discovery = await scan._wallets_for_tokens(client, chain, source, tokens)
        candidates = discovery["candidates"]
        selection = select_candidates(candidates, max_wallets)
        rows = []
        selected = selection["selected"]
        yield "progress", {"message": f"Selecting research candidates: {selection['eligible_count']} eligible; {selection['excluded_count']} excluded by app heuristics…", "candidates": len(candidates)}
        async def fetch_candidate(candidate):
            try:
                raw = await profile._fetch_core(client, chain, candidate["address"])
                summary = profile._summary(candidate["address"], chain, raw, 100)
                summary["recent_trades"] = profile._trades(chain, raw["trades"])
                for trade, source_trade in zip(summary["recent_trades"], raw["trades"]):
                    trade["usd"] = _f(source_trade.get("volume_in_usd"))
                summary["holdings"] = profile._holdings(raw["balances"])
                return evidence(candidate, summary)
            except Exception as exc:
                return {**candidate, "error": safe_error(exc)}

        pool = {}
        async for name, data in profile_batches(selected, fetch_candidate,
                target=config.TARGET_RESEARCHABLE_PROFILES, cap=max_wallets,
                batch_size=config.PROFILE_BATCH_SIZE, max_trades=max_trades):
            if name == "pool":
                pool = data
            else:
                yield name, data
        yield "progress", {"message": "Applying filters…"}
        result = {"run_id": secrets.token_urlsafe(16), "chain": chain, "tokens": tokens,
                  "candidate_count": len(candidates), **pool, "quality_max_trades": max_trades, "preselection": {k: v for k, v in selection.items() if k != "selected"}, "warnings": discovery.get("warnings", []),
                  "successful_requests": client.credits_used, "scanned_at": time.time()}
        result = normalize_pool(result)
        for store in (_cache, _runs):
            for old_key in list(store):
                if time.time() - store[old_key][0] >= 1800:
                    store.pop(old_key, None)
            if len(store) >= 24:
                store.pop(next(iter(store)))
        _cache[key] = (time.time(), result)
        _runs[result["run_id"]] = (time.time(), result)
        yield "result", result
