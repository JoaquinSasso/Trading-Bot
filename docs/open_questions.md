# Open Questions & API Discrepancies Log

This document records any discrepancies, ambiguities, or deviations between `docs/PLAN.md` and official external API documentation / real-world behavior (Finnhub REST, Alpaca APIs, BLS/Fed Macro Calendars).

| API Source | Plan Section | Issue Summary | Status | Resolution / Mitigation |
|---|---|---|---|---|
| Alpaca Market Data | Sec 7.2 | Statuses stream absent on free paper tier | OPEN | Preventatively treat symbols exceeding calibrated trade gap threshold as potential market halts. |
| Finnhub REST | Sec 8.3 | Earnings calendar hour field returns dmh code | OPEN | Implement `parse_hour_code()` normalizing `"bmo"`, `"amc"`, `"dmh"`, `""` to `"unspecified"`, and unmapped strings to `"unknown"`. |
| Alpaca Trading | Sec 4.2 | Fractional orders reject OCO/OTO execution | OPEN | Enforce native whole-share brackets for proxies (SPYM/QQQM), fallback to software guardian for fractional orders. |
| Alpaca Calendar | Sec 7.3 | Early close timestamp format variance | OPEN | Normalize early close timestamps to Eastern Time session bounds with dynamic timers. |
| Alpaca Stream | Sec 7.2 | WebSocket ping interval 10s vs 20s specification | OPEN | Implement adaptive heartbeat tolerance (60s global threshold) independent of server ping cadence. |
| Finnhub REST | Sec 8.3 | 60 req/min rate limit on free tier requires sliding rate limiter | RESOLVED | Built `AsyncRateLimiter` with token bucket and exponential backoff. |
| Finnhub REST | Sec 8.3 | Outage fail-closed policy requires 72h cache cutoff | RESOLVED | Strict 72.0 hour boundary check returning `EARNINGS_DATA_UNAVAILABLE`. |
| Macro Calendar YAML | Sec 8.3 | YAML naive ISO string requires America/New_York localization | RESOLVED | Timezone localized to `America/New_York` and converted to UTC timezone-aware datetimes. |
| Macro Calendar YAML | Sec 8.3 | FOMC swing lockout requires full trading day matching | RESOLVED | Comparison between current ET date and event ET date blocks swing entries for entire day. |
