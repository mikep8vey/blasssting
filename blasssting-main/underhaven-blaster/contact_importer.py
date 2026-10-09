"""UnderHaven Blaster public contact importer.

Supports ordinary HTML pages with requests/BeautifulSoup and uses a real,
headless Chromium browser for Google Maps pages because Maps renders its
business results client-side.

This module intentionally does not rotate proxies, bypass CAPTCHAs, defeat
access controls, or attempt to evade rate limits.
"""
import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().\-]{7,}\d)(?!\w)")
BAD_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "sentry.io"}
SOCIAL_PREFIXES = ("noreply@", "no-reply@", "donotreply@", "donot-reply@")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "Chrome/140.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_phone(value):
    value = clean_text(value)
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    if len(digits) < 8 or len(digits) > 15:
        return ""
    return ("+" if value.startswith("+") else "") + digits


def valid_email(value):
    value = value.strip().strip(".,;:()[]{}<>")
    low = value.lower()
    if "@" not in value or low in BAD_EMAIL_DOMAINS:
        return ""
    if any(low.startswith(x) for x in SOCIAL_PREFIXES):
        return ""
    return value


def unique_casefold(values):
    seen = set()
    result = []
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def extract_name(soup):
    selectors = [
        ('meta[property="og:site_name"]', "content"),
        ('meta[property="og:title"]', "content"),
        ('meta[name="application-name"]', "content"),
        ("h1", None),
        ("title", None),
    ]
    for selector, attr in selectors:
        node = soup.select_one(selector)
        if not node:
            continue
        value = node.get(attr, "") if attr else node.get_text(" ", strip=True)
        value = clean_text(value)
        value = re.sub(r"\s*[|•–—]\s*(home|contact|about|official site).*$", "", value, flags=re.I)
        if 2 <= len(value) <= 120:
            return value

    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if isinstance(obj, dict):
                name = clean_text(str(obj.get("name", "")))
                if 2 <= len(name) <= 120:
                    return name
    return ""


def extract_contacts(html):
    raw_soup = BeautifulSoup(html, "html.parser")
    emails = []
    phones = []

    for a in raw_soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            email = valid_email(href[7:].split("?", 1)[0])
            if email:
                emails.append(email)
        elif href.lower().startswith("tel:"):
            phone = normalize_phone(href[4:].split("?", 1)[0])
            if phone:
                phones.append(phone)

    for e in EMAIL_RE.findall(raw_soup.get_text(" ", strip=True)):
        email = valid_email(e)
        if email:
            emails.append(email)

    for p in PHONE_RE.findall(raw_soup.get_text(" ", strip=True)):
        phone = normalize_phone(p)
        if phone:
            phones.append(phone)

    return {
        "full_name": extract_name(raw_soup),
        "phone_number": unique_casefold(phones)[0] if phones else "",
        "email": unique_casefold(emails)[0] if emails else "",
    }


def _reject_private_host(hostname):
    host = (hostname or "").strip().lower()
    if host in {"localhost", "localhost.localdomain"} or not host:
        raise ValueError("Private or local URLs are not allowed.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        raise ValueError("The URL hostname could not be resolved.")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("Private or local URLs are not allowed.")


def _fetch_html(url, timeout=20):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter a complete http:// or https:// URL.")
    _reject_private_host(parsed.hostname)
    response = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    final_host = urlparse(response.url).hostname
    _reject_private_host(final_host)
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise ValueError("The URL did not return an HTML page.")
    return response


def _is_google_maps_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host == "maps.google.com" or host.endswith(".google.com") and parsed.path.startswith("/maps")


def _website_email(url, timeout=12):
    """Fetch a business website only when it is an external public HTTPS/HTTP URL."""
    if not url:
        return ""
    try:
        response = _fetch_html(url, timeout=timeout)
        record = extract_contacts(response.text)
        return record["email"]
    except Exception:
        return ""


def _maps_name(article):
    candidates = [
        'a[href*="/maps/place/"]',
        'div[role="heading"]',
        'h3',
    ]
    for selector in candidates:
        node = article.locator(selector).first
        try:
            if node.count() and node.is_visible():
                value = clean_text(node.inner_text())
                if value and value.lower() not in {"google maps", "directions"}:
                    return value
        except Exception:
            pass
    return ""


def _maps_attr(article, selectors):
    for selector in selectors:
        try:
            loc = article.locator(selector).first
            if loc.count():
                href = loc.get_attribute("href") or ""
                aria = loc.get_attribute("aria-label") or ""
                title = loc.get_attribute("title") or ""
                value = href or aria or title
                if value:
                    return value
        except Exception:
            continue
    return ""


def import_google_maps(url, max_results=50, timeout_ms=45000):
    """Render a Google Maps page and extract visible business contacts.

    A search URL can return multiple business records. A place URL normally
    returns one. Email is supplemented from the public business website when
    a website link is exposed by Maps.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Google Maps importing requires Playwright/Chromium. "
            "Run install.sh again or install the Playwright browser."
        ) from exc

    parsed = urlparse(url)
    _reject_private_host(parsed.hostname)
    records = []

    with sync_playwright() as p:
        chromium_path = os.getenv("CHROMIUM_PATH", "").strip()
        if not chromium_path:
            for candidate in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
                if os.path.exists(candidate):
                    chromium_path = candidate
                    break
        launch_kwargs = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="en-US")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(4000)

            # Dismiss common consent/interstitial buttons when present. This is
            # normal page interaction, not a CAPTCHA or access-control bypass.
            for label in ["Accept all", "I agree", "Got it"]:
                try:
                    button = page.get_by_role("button", name=re.compile(re.escape(label), re.I)).first
                    if button.count() and button.is_visible():
                        button.click(timeout=1500)
                        page.wait_for_timeout(1200)
                        break
                except Exception:
                    pass

            # Search result feeds lazy-load. Scroll a bounded number of times.
            feed = page.locator('div[role="feed"]').first
            if feed.count():
                for _ in range(8):
                    try:
                        feed.evaluate("el => el.scrollTop = el.scrollHeight")
                    except Exception:
                        break
                    page.wait_for_timeout(800)

            articles = page.locator('div[role="article"]')
            count = min(articles.count(), max_results)
            for i in range(count):
                article = articles.nth(i)
                name = _maps_name(article)
                if not name:
                    continue

                phone_raw = _maps_attr(article, [
                    '[data-item-id*="phone"]',
                    'a[href^="tel:"]',
                    'button[aria-label*="Phone"]',
                ])
                phone = normalize_phone(phone_raw.replace("Phone:", "", 1) if phone_raw else "")
                if not phone and phone_raw.startswith("tel:"):
                    phone = normalize_phone(phone_raw[4:])

                website = _maps_attr(article, [
                    'a[data-item-id="authority"]',
                    'a[aria-label*="Website"]',
                ])
                if website.startswith("/"):
                    website = ""

                email = _website_email(website) if website else ""
                records.append({
                    "full_name": name,
                    "phone_number": phone,
                    "email": email,
                })

            # Place pages may not expose a role=article card. Extract the
            # currently displayed place directly as a fallback.
            if not records:
                body = page.locator("body").inner_text(timeout=5000)
                name = ""
                for selector in ["h1", 'div[role="main"] h1']:
                    loc = page.locator(selector).first
                    try:
                        if loc.count():
                            name = clean_text(loc.inner_text())
                            if name:
                                break
                    except Exception:
                        pass
                phones = [normalize_phone(x) for x in PHONE_RE.findall(body)]
                phones = [x for x in phones if x]
                emails = [valid_email(x) for x in EMAIL_RE.findall(body)]
                emails = [x for x in emails if x]
                if name:
                    records.append({
                        "full_name": name,
                        "phone_number": unique_casefold(phones)[0] if phones else "",
                        "email": unique_casefold(emails)[0] if emails else "",
                    })
        finally:
            browser.close()

    # Remove duplicate businesses while preserving order.
    seen = set()
    clean = []
    for record in records:
        key = (
            record.get("full_name", "").casefold(),
            record.get("phone_number", ""),
            record.get("email", "").casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        clean.append(record)
    return clean


def import_url(url, timeout=20):
    url = (url or "").strip()
    if not url:
        raise ValueError("URL is required.")

    if _is_google_maps_url(url):
        records = import_google_maps(url)
        if not records:
            raise ValueError(
                "Google Maps loaded, but no business records were found. "
                "The page may require an interactive browser session or may have returned a consent/CAPTCHA page."
            )
        return {
            "records": records,
            "source_url": url,
            "count": len(records),
            "full_name": records[0].get("full_name", ""),
            "phone_number": records[0].get("phone_number", ""),
            "email": records[0].get("email", ""),
        }

    response = _fetch_html(url, timeout=timeout)
    record = extract_contacts(response.text)
    record["source_url"] = response.url
    record["http_status"] = response.status_code
    record["records"] = [record.copy()]
    record["count"] = 1
    return record
