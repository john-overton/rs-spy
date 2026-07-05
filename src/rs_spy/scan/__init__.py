"""Nightly universe scan (algo-spec 01 §4) -- the "what to trade" discovery half.

Self-computed from broad Alpaca daily bars so one code path serves both the
live nightly scan (as_of=today) and point-in-time reconstruction (as_of=any
cached date). Spec: docs/superpowers/specs/2026-07-05-universe-scan-design.md.

Disclosed limits (deliberate, documented, not silent):
  * No security-type or float data from Alpaca: ETF exclusion is a
    name/exchange heuristic; the float>=50M gate is substituted by the
    dollar-volume floor (see scan/config.py).
  * The halt-history gate (01 §4.5) is dropped -- no historical halt feed.
  * Point-in-time reconstruction uses the CURRENT asset list (survivorship
    bias: symbols delisted before today are absent).
  * Free-tier IEX volume is ~2-3% of consolidated SIP volume; ScanConfig
    carries per-feed thresholds so a paid SIP upgrade is config, not code.
"""
