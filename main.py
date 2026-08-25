import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import feedparser
from google import genai
from google.genai import types

# --- 1. CONFIGURATION & ENVIRONMENT VARIABLES ---
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", GMAIL_USER)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Mapping Sectors to RSS Feeds (Increased sample size to feed up to 15 articles)
SECTOR_FEEDS = {
    "📈 Indonesian Business And Markets": [
        "https://www.cnbcindonesia.com/market/rss",
        "https://news.google.com/rss/search?q=IHSG+OR+ekonomi+indonesia&hl=id&gl=ID",
    ],
    "💻 Global Tech & Earnings": [
        "https://news.google.com/rss/search?q=Alphabet+earnings+OR+Big+Tech+earnings&hl=en-US&gl=US",
        "https://feeds.feedburner.com/TechCrunch/",
    ],
    "🇮🇩 Indonesian National News": [
        "https://www.cnnindonesia.com/nasional/rss",
        "https://rss.tempo.co/nasional",
    ],
    "🏛️ Politics & Geopolitics": [
        "https://www.cnnindonesia.com/politik/rss",
        "https://news.google.com/rss/search?q=geopolitics+OR+world+politics&hl=en-US&gl=US",
    ],
    "🎬 Pop Culture & Entertainment": [
        "https://www.cnnindonesia.com/hiburan/rss",
        "https://news.google.com/rss/search?q=pop+culture+news&hl=en-US&gl=US",
    ],
}

# --- 2. FETCH ARTICLES ---
def fetch_sector_articles():
    sector_data = {}
    for sector, urls in SECTOR_FEEDS.items():
        articles = []
        for url in urls:
            try:
                feed = feedparser.parse(url)
                # Grab up to 8 articles per feed source to allow a deep list per sector
                for entry in feed.entries[:8]:
                    title = entry.get("title", "No Title")
                    link = entry.get("link", "#")
                    summary = entry.get("summary", entry.get("description", ""))
                    clean_summary = re.sub(r"<[^>]+>", "", summary).strip()
                    articles.append(
                        f"- Title: {title}\n  URL: {link}\n  Snippet: {clean_summary}"
                    )
            except Exception as e:
                print(f"Error fetching RSS feed {url}: {e}")
        sector_data[sector] = "\n".join(articles)
    return sector_data

# --- 3. AI TRANSLATE & SUMMARIZE VIA GEMINI ---
def generate_digest(sector_data):
    client = genai.Client(api_key=GEMINI_API_KEY)

    formatted_input = ""
    for sector, text in sector_data.items():
        formatted_input += f"\n=== SECTOR: {sector} ===\n{text}\n"

    prompt = f"""
    You are an executive news editor. I will provide raw article snippets with titles and URLs across 5 categories in English and Indonesian.

    Task:
    Translate all Indonesian text into clear, professional English and format EACH category using the EXACT structural template outlined below.

    Formatting Structure required per Category/Sector:
    
    1. Category Header: `<h2>[Sector Name]</h2>` (Include relevant emojis inside headers)
    2. Executive Synthesis Paragraph: 
       Write a narrative overview synthesizing the big picture, major gainers/losers, market mood/state, key technical reasons, and major macro figures mentioned across the news. Sprinkle relevant emojis dynamic into the narrative. Reference underlying driver themes/articles inline.
    3. Article Breakdown List: 
       Following the paragraph summary, output a comprehensive list (`<ul>`) of headline bullet points covering the individual stories provided. 
       - Each bullet headline MUST be wrapped inside a clickable HTML link `<a href="URL">` pointing to the article URL.
       - Under each bullet point title, include a concise 1-2 sentence summary of that specific story. Continue this for as many distinct articles as provided (up to 10-15 stories per sector).

    Example layout to output for each sector:
    <div>
      <h2 style="color: #1a365d; border-bottom: 2px solid #3182ce; padding-bottom: 5px;">[Emoji] [Sector Name]</h2>
      <p style="font-size: 14px; line-height: 1.6; color: #2d3748;">
        [Overview narrative synthesizing macro state, market sentiment, key indicators/numbers, and technical drivers based on current news summaries...] 📈📊
      </p>
      <ul style="padding-left: 20px; color: #2d3748;">
        <li style="margin-bottom: 10px;">
          <strong><a href="URL" style="text-decoration: none; color: #2b6cb0;">[Headline Title 1]</a></strong><br/>
          <span style="font-size: 13px; color: #4a5568;">[Summary of article 1]</span>
        </li>
        <li style="margin-bottom: 10px;">
          <strong><a href="URL" style="text-decoration: none; color: #2b6cb0;">[Headline Title 2]</a></strong><br/>
          <span style="font-size: 13px; color: #4a5568;">[Summary of article 2]</span>
        </li>
        <!-- repeat for all articles -->
      </ul>
    </div>

    Formatting Rules:
    - Output ONLY raw HTML inside container `<div>` elements. Do NOT use markdown code fences (```html).
    - Ensure clean inline CSS styles for high readability in email clients.
    - Sprinkle relevant emojis dynamically across headers, executive syntheses, and headlines to keep it engaging 🚀🔥.
    - Make sure EVERY story headline is hyperlinked to its respective article URL.

    Raw Articles:
    {formatted_input}
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
        ),
    )

    clean_html = response.text.strip()
    clean_html = re.sub(r"^```html\s*", "", clean_html, flags=re.MULTILINE)
    clean_html = re.sub(r"^```\s*$", "", clean_html, flags=re.MULTILINE)

    return clean_html

# --- 4. GMAIL DISPATCHER ---
def send_email(html_body):
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise ValueError("Missing GMAIL_USER or GMAIL_APP_PASSWORD environment variables.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "☕ Daily Market Overview & News Breakdown 📰"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL

    html_part = MIMEText(html_body, "html")
    msg.attach(html_part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
    print("Email sent successfully!")

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("Fetching news feeds...")
    raw_data = fetch_sector_articles()

    print("Summarizing & Formatting via Gemini...")
    digest_html = generate_digest(raw_data)

    print("Sending Gmail...")
    send_email(digest_html)