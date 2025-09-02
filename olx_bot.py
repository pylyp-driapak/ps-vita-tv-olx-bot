import requests
from bs4 import BeautifulSoup
import json
import time
import logging
import os
from urllib.parse import urljoin

# Telegram setup
# Prefer environment variables if provided
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

def _token_format_invalid(token: str) -> bool:
    # Telegram tokens typically look like "123456789:AA..." (contain a colon)
    return not token or ":" not in token

def send_text_message(text, parse_mode=None):
    if _token_format_invalid(BOT_TOKEN):
        logging.error("Telegram BOT token format looks invalid. Set TELEGRAM_BOT_TOKEN env var (format '123456789:...').")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    try:
        resp = requests.post(url, data=data, timeout=10)
        if resp.status_code != 200:
            try:
                payload = resp.json()
            except Exception:
                payload = {"raw": resp.text[:200]}
            if resp.status_code == 404:
                logging.warning("Telegram send failed (404 Not Found). Check BOT token and endpoint. Response: %s", payload)
            elif resp.status_code == 403:
                logging.warning("Telegram send failed (403 Forbidden). Likely causes: user/group hasn't started the bot, CHAT_ID points to a bot, or the bot lacks permission in the chat. Response: %s", payload)
            else:
                logging.warning("Telegram send failed (%s): %s", resp.status_code, payload)
            return False
        logging.debug("Telegram message sent successfully")
        return True
    except Exception as e:
        logging.error("Telegram send exception: %s", e)
        return False

# Storage for seen ads
SEEN_FILE = "seen_ads.json"
try:
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        seen_ads = set(json.load(f))
        logging.info("Loaded %d seen ads from %s", len(seen_ads), SEEN_FILE)
except Exception as e:
    logging.warning("Could not load seen ads (%s). Starting fresh.", e)
    seen_ads = set()

# OLX search queries
QUERIES = [
    "https://www.olx.ua/uk/list/q-playstation-tv/"
]

# Title keywords filter (case-insensitive). All must be present.
REQUIRED_KEYWORDS = [
    kw.strip().lower()
    for kw in os.getenv("OLX_TITLE_KEYWORDS", "tv,playstation").split(",")
    if kw.strip()
]

def fetch_ads(url):
    logging.info("Fetching URL: %s", url)
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logging.error("Failed to fetch %s: %s", url, e)
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    ads = []

    for item in soup.select("div[data-cy='l-card']"):
        # Prefer data attributes and structure over CSS classes
        title_container = item.select_one("div[data-cy='ad-card-title']")
        title_tag = None
        link_tag = None
        price_tag = None

        if title_container:
            title_tag = title_container.find("h4") or title_container.find("h3")
            link_tag = title_container.find("a", href=True)
            price_tag = item.select_one("p[data-testid='ad-price']")
        else:
            # Fallbacks if the above structure changes
            title_tag = item.find(["h4", "h3", "h6"])  # try common heading tags
            # Choose the first anchor that looks like an ad link
            for a in item.find_all("a", href=True):
                href = a.get("href", "")
                if href.startswith("/d/") or href.startswith("http"):
                    link_tag = a
                    break
            price_tag = item.find("p", attrs={"data-testid": "ad-price"}) or item.find("p")

        if link_tag and title_tag:
            href = link_tag.get("href", "")
            absolute_url = urljoin("https://www.olx.ua", href)
            ad_id = item.get("id") or href
            title_text = title_tag.get_text(strip=True)
            price_text = price_tag.get_text(strip=True) if price_tag else "N/A"
            # Filter by required keywords in title
            lowered = title_text.lower()
            missing = [kw for kw in REQUIRED_KEYWORDS if kw not in lowered]
            if missing:
                logging.debug("Skipped ad missing keywords %s: %s", missing, title_text)
                continue

            ads.append({
                "id": ad_id,
                "title": title_text,
                "price": price_text,
                "url": absolute_url
            })
    logging.info("Parsed %d ads from %s", len(ads), url)
    return ads

def check_new_ads():
    global seen_ads
    new_ads = []
    for url in QUERIES:
        ads = fetch_ads(url)
        new_for_url = 0
        for ad in ads:
            if ad["id"] not in seen_ads:
                seen_ads.add(ad["id"])
                new_ads.append(ad)
                new_for_url += 1
        logging.info("URL done: %s | total=%d, new=%d", url, len(ads), new_for_url)
    logging.info("Check complete. Total new ads found: %d", len(new_ads))
    return new_ads

def send_ads(ads):
    for ad in ads:
        message = f"📢 *{ad['title']}*\n💰 {ad['price']}\n🔗 {ad['url']}"
        ok = send_text_message(message, parse_mode="Markdown")
        if ok:
            logging.info("Sent ad: %s", ad["title"]) 
        else:
            logging.warning("Failed to send ad: %s", ad["title"]) 

def save_seen():
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ads), f)
    logging.info("Saved %d seen ads to %s", len(seen_ads), SEEN_FILE)

if __name__ == "__main__":
    logging.info("Bot starting. Monitoring %d query URLs.", len(QUERIES))
    logging.info("Telegram chat: %s | Token format valid: %s", CHAT_ID, not _token_format_invalid(BOT_TOKEN))
    try:
        bot_id = BOT_TOKEN.split(":")[0]
        if CHAT_ID == bot_id:
            logging.warning("CHAT_ID equals the bot's own ID (%s). Set CHAT_ID to your user/group ID, not the bot ID.", bot_id)
    except Exception:
        pass
    # Notify when the bot starts
    # send_text_message("🤖 OLX bot started. Monitoring queries.")
    logging.info("Starting single check cycle…")
    new_ads = check_new_ads()
    if new_ads:
        send_ads(new_ads)
        save_seen()
    else:
        logging.info("No new ads this cycle.")
    # Notify after the check completes
    # send_text_message(f"⏱️ Check complete: {len(new_ads)} new ad(s).")
    logging.info("Single run complete.")
