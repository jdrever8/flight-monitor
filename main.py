import requests
import schedule
import time
import json
import os
from datetime import datetime, timedelta
from amadeus import Client, ResponseError

# ── CONFIG ────────────────────────────────────────────────────────────────────
AMADEUS_CLIENT_ID     = "X7AGp4hPxhFZdxmbf6s6DXKeUbmNa5Go"
AMADEUS_CLIENT_SECRET = "6NH6qhj5ImZ1Hz0l"
TELEGRAM_TOKEN        = "8642630481:AAEK9dpGMMMrz4NSJk40NhtpOgekM5pk6TI"
TELEGRAM_CHAT_ID      = "5951712775"

# Origin airports to scan FROM
ORIGINS = ["EDI", "GLA"]

# Alert if price is this % below the stored average (0.55 = 45% cheaper than normal)
ALERT_THRESHOLD_PCT = 0.55

# Maximum price cap in GBP — ignore anything above this even if it's a "deal"
MAX_PRICE_GBP = 800

# Trip duration range in days (min, max) — covers weekends to long holidays
DURATION_MIN = 3
DURATION_MAX = 21

# How far ahead to search (days from today)
DEPART_WINDOW_START = 14   # At least 2 weeks away
DEPART_WINDOW_END   = 180  # Up to 6 months away

PRICE_HISTORY_FILE = "price_history.json"
CHECK_INTERVAL_HOURS = 2
# ─────────────────────────────────────────────────────────────────────────────

amadeus = Client(client_id=AMADEUS_CLIENT_ID, client_secret=AMADEUS_CLIENT_SECRET)


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"Telegram error: {e}")


def load_history() -> dict:
    if os.path.exists(PRICE_HISTORY_FILE):
        with open(PRICE_HISTORY_FILE, "r") as f:
            return json.load(f)
    return {}


def save_history(history: dict):
    with open(PRICE_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def update_baseline(key: str, price: float, history: dict):
    if key not in history:
        history[key] = []
    history[key].append(round(price, 2))
    history[key] = history[key][-30:]  # Rolling 30 observations


def get_inspirations(origin: str) -> list:
    """
    Uses Flight Inspiration Search to get cheapest destinations
    from origin across all dates and durations — fully open ended.
    Returns list of dicts with destination, price, departure/return dates.
    """
    depart_start = (datetime.today() + timedelta(days=DEPART_WINDOW_START)).strftime("%Y-%m-%d")
    depart_end   = (datetime.today() + timedelta(days=DEPART_WINDOW_END)).strftime("%Y-%m-%d")

    try:
        response = amadeus.shopping.flight_destinations.get(
            origin=origin,
            departureDate=f"{depart_start},{depart_end}",
            duration=f"{DURATION_MIN},{DURATION_MAX}",
            oneWay=False,
            nonStop=False,
            maxPrice=MAX_PRICE_GBP,
            currencyCode="GBP",
            viewBy="DESTINATION"
        )
        return response.data if response.data else []
    except ResponseError as e:
        print(f"  Amadeus inspiration error ({origin}): {e}")
        return []


def check_flights():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Running open-ended scan...")
    history = load_history()
    alerts = []

    for origin in ORIGINS:
        print(f"  Scanning all destinations from {origin}...")
        destinations = get_inspirations(origin)

        if not destinations:
            print(f"  No results returned for {origin} — may be rate limited or test env.")
            continue

        print(f"  {len(destinations)} destinations found from {origin}")

        for item in destinations:
            try:
                destination  = item["destination"]
                price        = float(item["price"]["total"])
                depart_date  = item.get("departureDate", "N/A")
                return_date  = item.get("returnDate", "N/A")
                key          = f"{origin}-{destination}"

                # Always update baseline
                update_baseline(key, price, history)

                # Only alert once we have enough history (5+ observations)
                if len(history[key]) >= 5:
                    baseline = sum(history[key][:-1]) / len(history[key][:-1])
                    ratio    = price / baseline

                    if ratio <= ALERT_THRESHOLD_PCT:
                        drop_pct = round((1 - ratio) * 100)
                        duration_days = ""
                        if depart_date != "N/A" and return_date != "N/A":
                            d1 = datetime.strptime(depart_date, "%Y-%m-%d")
                            d2 = datetime.strptime(return_date, "%Y-%m-%d")
                            duration_days = f" ({(d2-d1).days} nights)"

                        alerts.append(
                            f"✈️ <b>DEAL: {origin} → {destination}</b>\n"
                            f"📅 Depart: {depart_date} | Return: {return_date}{duration_days}\n"
                            f"💷 Price: <b>£{price:.2f}</b>\n"
                            f"📉 <b>{drop_pct}% below average</b> (avg: £{baseline:.2f})\n"
                            f"🔗 https://www.skyscanner.net/transport/flights/"
                            f"{origin.lower()}/{destination.lower()}/"
                        )

            except (KeyError, ValueError) as e:
                print(f"  Skipping entry due to error: {e}")
                continue

    save_history(history)

    if alerts:
        # Send a summary header first
        send_telegram(f"🚨 <b>{len(alerts)} DEAL(S) FOUND</b> — {datetime.now().strftime('%d %b %Y %H:%M')}")
        for alert in alerts:
            send_telegram(alert)
            time.sleep(0.5)  # Slight delay to avoid Telegram rate limiting
        print(f"  ✅ Sent {len(alerts)} alert(s) to Telegram!")
    else:
        print("  No deals found this run — baselines updated.")


# ── SCHEDULER ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🛫 Open-Ended Flight Deal Monitor Started")
    print(f"   Origins:        {', '.join(ORIGINS)}")
    print(f"   Destinations:   ALL (open-ended)")
    print(f"   Duration:       {DURATION_MIN}–{DURATION_MAX} nights")
    print(f"   Depart window:  {DEPART_WINDOW_START}–{DEPART_WINDOW_END} days from today")
    print(f"   Max price:      £{MAX_PRICE_GBP}")
    print(f"   Alert if:       {round((1-ALERT_THRESHOLD_PCT)*100)}%+ below average\n")

    send_telegram(
        f"✅ <b>Flight Monitor Live</b>\n"
        f"Scanning ALL destinations from EDI & GLA\n"
        f"Duration: {DURATION_MIN}–{DURATION_MAX} nights | Max: £{MAX_PRICE_GBP}\n"
        f"Checking every {CHECK_INTERVAL_HOURS} hours 🔍"
    )

    check_flights()  # Run immediately on start

    schedule.every(CHECK_INTERVAL_HOURS).hours.do(check_flights)

    while True:
        schedule.run_pending()
        time.sleep(60)
