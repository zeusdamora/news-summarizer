import json
import os
import re
import smtplib
import time
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import feedparser
from google import genai
from google.genai import types

# Load local .env file if running locally
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- CONFIGURATION ---
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", GMAIL_USER)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

CACHE_FILE = "seen_urls.json"
MAX_ARTICLE_AGE_HOURS = 24

# Verify environment variables
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing.")
if not GMAIL_USER or not GMAIL_APP_PASSWORD:
    raise ValueError("GMAIL_USER or GMAIL_APP_PASSWORD is missing.")

RSS_FEEDS = {
    "Macroeconomics & Markets": "https://investing.com/rss/news.rss",
    "Indonesian Stock Market (IHSG)": "https://www.cnbcindonesia.com/market/rss",
    "Global Tech & AI": "https://techcrunch.com/feed/",
    "Palembang & South Sumatra Local": "https://sumsel.antaranews.com/rss/berita-palembang.xml",
    "Business & Entrepreneurship": "https://feeds.feedburner.com/entrepreneur/latest"
}

# Non-news / promotional keywords for Python-level filtering
EXCLUDED_KEYWORDS = [
    "sponsored", "promoted", "how to", "opinion:", "editorial:",
    "top 10", "best deals", "guide to", "review:"
]

# --- HELPER FUNCTIONS ---

def load_seen_urls():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen_urls(seen_urls):
    with open(CACHE_FILE, "w") as f:
        json.dump(list(seen_urls), f, indent=2)

def is_recent(entry, max_hours=24):
    """Check if the feed entry was published within the last max_hours."""
    published_parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not published_parsed:
        return True  # Retain if no timestamp provided by RSS source
    
    try:
        entry_time = datetime.fromtimestamp(time.mktime(published_parsed), tz=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - entry_time) <= timedelta(hours=max_hours)
    except Exception:
        return True

def is_valid_news(title, summary):
    """Filter out non-news articles using Python rules."""
    combined = f"{title} {summary}".lower()
    for kw in EXCLUDED_KEYWORDS:
        if kw in combined:
            return False
    return True

def fetch_rss_articles():
    seen_urls = load_seen_urls()
    new_seen_urls = set(seen_urls)
    feed_data = {}

    for category, url in RSS_FEEDS.items():
        feed = feedparser.parse(url)
        feed_data[category] = []
        
        for entry in feed.entries:
            link = getattr(entry, "link", "").strip()
            title = getattr(entry, "title", "").strip()
            
            if not link or not title or link in new_seen_urls:
                continue
            
            if not is_recent(entry, max_hours=MAX_ARTICLE_AGE_HOURS):
                continue

            summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            clean_summary = re.sub('<[^<]+?>', '', summary).strip()

            if not is_valid_news(title, clean_summary):
                continue

            feed_data[category].append({
                "title": title,
                "link": link,
                "summary": clean_summary[:300]
            })
            new_seen_urls.add(link)

    save_seen_urls(new_seen_urls)
    return feed_data

def process_with_gemini(feed_data):
    """Pass clean data to Gemini for translation & concise bullet generation using structured JSON."""
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
You are an executive translator and summarizer.
Your task is ONLY to translate (if in Indonesian) and summarize the provided news items into clean English, grouped by their original categories.

INPUT DATA:
{json.dumps(feed_data, indent=2)}

RULES:
1. Do NOT add, remove, or alter any article URLs.
2. Maintain exact article mappings.
3. If an article is in Indonesian, translate it into professional executive-level English.
4. Keep summaries strictly factual based ONLY on the provided summary text.
"""

    # Schema compatible with Gemini Developer API (using array of categories instead of additionalProperties)
    json_schema = {
        "type": "OBJECT",
        "properties": {
            "market_overview": {
                "type": "STRING",
                "description": "2-3 sentence overall summary of the day's top news stories."
            },
            "categories": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "category_name": {"type": "STRING"},
                        "items": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "emoji": {"type": "STRING"},
                                    "headline": {"type": "STRING"},
                                    "link": {"type": "STRING"},
                                    "summary": {"type": "STRING"}
                                },
                                "required": ["emoji", "headline", "link", "summary"]
                            }
                        }
                    },
                    "required": ["category_name", "items"]
                }
            }
        },
        "required": ["market_overview", "categories"]
    }

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=json_schema
        )
    )
    
    return json.loads(response.text)

def render_html_digest(processed_data):
    """Python renders the final HTML safely to guarantee strict formatting."""
    overview = processed_data.get("market_overview", "")
    categories = processed_data.get("categories", [])

    html_parts = []
    
    if overview:
        html_parts.append(f"""
        <div style="background-color: #f3f4f6; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
          <h2 style="font-size: 18px; color: #111827; margin-top: 0;">Executive Overview</h2>
          <p style="margin: 0; color: #374151;">{overview}</p>
        </div>
        """)

    for cat in categories:
        cat_name = cat.get("category_name", "News")
        items = cat.get("items", [])
        if not items:
            continue
        
        html_parts.append(f'<h2 style="font-size: 16px; color: #1f2937; border-bottom: 1px solid #e5e7eb; padding-bottom: 5px;">{cat_name}</h2>')
        html_parts.append('<ul style="padding-left: 20px; margin-bottom: 25px;">')
        
        for item in items:
            emoji = item.get("emoji", "📰")
            headline = item.get("headline", "")
            link = item.get("link", "#")
            summary = item.get("summary", "")

            html_parts.append(f"""
            <li style="margin-bottom: 12px;">
              <strong><a href="{link}" style="color: #2563eb; text-decoration: none;">{emoji} {headline}</a></strong> — {summary}
            </li>
            """)
        
        html_parts.append('</ul>')

    return "\n".join(html_parts)

def send_email(html_body):
    today = datetime.now().strftime("%B %d, %Y")
    subject = f"📰 Executive News Digest — {today}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL

    full_html = f"""
    <html>
      <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1f2937; line-height: 1.6; max-width: 680px; margin: 0 auto; padding: 20px;">
        <div style="border-bottom: 2px solid #e5e7eb; padding-bottom: 15px; margin-bottom: 25px;">
          <h1 style="font-size: 24px; color: #111827; margin: 0;">Daily Executive News Digest</h1>
          <p style="color: #6b7280; font-size: 14px; margin: 5px 0 0 0;">Palembang Time • {today}</p>
        </div>
        
        {html_body}

        <div style="border-top: 1px solid #e5e7eb; margin-top: 35px; padding-top: 15px; text-align: center; color: #9ca3af; font-size: 12px;">
          Automated via Gemini 2.5 Flash & GitHub Actions
        </div>
      </body>
    </html>
    """

    msg.attach(MIMEText(full_html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())

def main():
    print("Fetching and filtering RSS articles in Python...")
    feed_data = fetch_rss_articles()
    
    total_articles = sum(len(items) for items in feed_data.values())
    print(f"Total verified articles parsed: {total_articles}")
    
    if total_articles == 0:
        print("No new articles found. Skipping email.")
        return

    print("Summarizing & translating with Gemini 2.5 Flash (Structured Output)...")
    processed_data = process_with_gemini(feed_data)

    print("Rendering HTML email...")
    digest_html = render_html_digest(processed_data)

    print("Sending email...")
    send_email(digest_html)
    print("Digest successfully sent!")

if __name__ == "__main__":
    main()