"""$LEAF mint watcher. Runs on a GitHub Actions cron and pushes to ntfy on state changes."""
import json
import os
import sys
import urllib.request

LEAF = "https://leaficobtc.vercel.app/api"
ORDI_PER_BTC_POINTS = 7000  # fixed protocol ratio: 1 BTC counts as 7000 ORDI
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
TOPIC = os.environ.get("NTFY_TOPIC", "")
FORCE_PING = os.environ.get("FORCE_PING") == "1"
MILESTONES = [75, 90, 99, 100]
UA = {"User-Agent": "Mozilla/5.0 leaf-alert"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def prices():
    """(btc_usd, ordi_usd) from the first source that answers. GitHub runners are US-based, so Binance goes last."""
    sources = [
        lambda: (lambda d: (d["bitcoin"]["usd"], d["ordinals"]["usd"]))(
            get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ordinals&vs_currencies=usd")),
        lambda: (float(get("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT")["data"][0]["last"]),
                 float(get("https://www.okx.com/api/v5/market/ticker?instId=ORDI-USDT")["data"][0]["last"])),
        lambda: (float(get("https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT")["result"]["list"][0]["lastPrice"]),
                 float(get("https://api.bybit.com/v5/market/tickers?category=spot&symbol=ORDIUSDT")["result"]["list"][0]["lastPrice"])),
        lambda: tuple(float(x["price"]) for x in sorted(
            get('https://api.binance.com/api/v3/ticker/price?symbols=["BTCUSDT","ORDIUSDT"]'),
            key=lambda x: x["symbol"])),
    ]
    for src in sources:
        try:
            btc, ordi = src()
            if btc > 0 and ordi > 0:
                return float(btc), float(ordi)
        except Exception:
            continue
    return None


def band(discount):
    if discount >= 2.0:
        return "big"
    if discount >= 1.5:
        return "ok"
    if discount >= 1.1:
        return "thin"
    return "gone"


BAND_TEXT = {
    "big": "ORDI is still the cheap way in",
    "ok": "ORDI discount narrowing",
    "thin": "ORDI discount nearly gone",
    "gone": "ORDI discount gone, BTC and ORDI cost the same",
}


def push(title, body, priority="default", tags="seedling"):
    print(f"PUSH [{priority}] {title}: {body}")
    if not TOPIC:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{TOPIC}", data=body.encode(), method="POST",
        headers={"Title": title, "Priority": priority, "Tags": tags,
                 "Click": "https://leaficobtc.vercel.app/"})
    urllib.request.urlopen(req, timeout=20).read()


def main():
    try:
        state = json.load(open(STATE_FILE))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    before = json.dumps(state, sort_keys=True)
    first_run = "band" not in state

    # 1. Site health. Three failed runs in a row (~30 min) is worth knowing about.
    try:
        prog = get(f"{LEAF}/mint-progress?tick=LEAF")
        acts = get(f"{LEAF}/activity?tick=LEAF&offset=0")["data"]
        site_ok = True
    except Exception as e:
        print("leaf api error:", e)
        site_ok = False

    if not site_ok:
        state["fails"] = state.get("fails", 0) + 1
        if state["fails"] == 3:
            push("LEAF site down", "leaficobtc.vercel.app API has failed 3 checks in a row (~30 min).",
                 "high", "warning")
    else:
        if state.get("fails", 0) >= 3:
            push("LEAF site back", "The LEAF API is answering again.", "default", "white_check_mark")
        state["fails"] = 0

    # 2. ORDI discount band.
    px = prices()
    discount = None
    if px:
        btc, ordi = px
        discount = (btc / ordi) / ORDI_PER_BTC_POINTS
        b = band(discount)
        if b != state.get("band") and not first_run:
            prio = "high" if b in ("thin", "gone") else "default"
            push(BAND_TEXT[b],
                 f"Paying with ORDI is now {discount:.2f}x cheaper than BTC "
                 f"(BTC ${btc:,.0f}, ORDI ${ordi:.3f}).", prio, "chart_with_downwards_trend")
        state["band"] = b
        state["discount"] = round(discount, 3)

    if site_ok:
        pct = float(prog["progress_percent"])
        state["progress"] = round(pct, 2)
        hit = [m for m in MILESTONES if pct >= m and m not in state.get("milestones", [])]
        if hit and not first_run:
            m = max(hit)
            push(f"LEAF mint {m}% done" if m < 100 else "LEAF minted out",
                 f"{pct:.1f}% of 1B minted, {prog['participants']} holders.",
                 "high" if m >= 90 else "default", "hourglass")
        state["milestones"] = sorted(set(state.get("milestones", [])) |
                                     {m for m in MILESTONES if pct >= m})

        # 3. First sign of LEAF moving outside the mint: a transfer or marketplace trade.
        seen = set(state.get("seen_non_mint", []))
        new = [a for a in acts
               if (a.get("op") not in ("intent", "mint") or a.get("is_marketplace"))
               and a["txid"] not in seen]
        if new:
            a = new[0]
            kind = "marketplace trade" if a.get("is_marketplace") else f"'{a.get('op')}' op"
            push("LEAF is moving outside the mint",
                 f"First {kind} seen in the LEAF indexer (tx {a['txid'][:12]}...). "
                 "A secondary market may be opening, so check UniSat and Magic Eden.",
                 "urgent", "rotating_light")
            state["seen_non_mint"] = sorted(seen | {x["txid"] for x in new})[-200:]

    if first_run or FORCE_PING:
        d = f"{discount:.2f}x" if discount else "n/a (no price source)"
        push("LEAF alert armed" if first_run else "LEAF status",
             f"ORDI discount {d}. Mint {state.get('progress', '?')}% done. "
             f"Site {'up' if site_ok else 'DOWN'}.", "low", "seedling")

    after = json.dumps(state, sort_keys=True)
    if after != before:
        json.dump(state, open(STATE_FILE, "w"), indent=2, sort_keys=True)
        print("state changed")
    if not px and not site_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
