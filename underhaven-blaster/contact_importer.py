"""
UnderHaven Blaster - public contact importer.

Designed for public pages the operator is authorized to process.
It does not rotate proxies, bypass CAPTCHAs, defeat access controls,
or attempt to evade rate limits.
"""
import re
import ipaddress
import socket
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().\-]{7,}\d)(?!\w)")
BAD_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "sentry.io"}
SOCIAL_PREFIXES = ("noreply@", "no-reply@", "donotreply@", "donot-reply@")

HEADERS = {
    "User-Agent": "UnderHaven-ContactImporter/1.0 (+public-page-contact-import)",
    "Accept": "text/html,application/xhtml+xml",
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
    # Preserve international + when present.
    return ("+" if value.startswith("+") else "") + digits

def valid_email(value):
    value = value.strip().strip(".,;:()[]{}<>")
    low = value.lower()
    if "@" not in value or low in BAD_EMAIL_DOMAINS:
        return ""
    if any(low.startswith(x) for x in SOCIAL_PREFIXES):
        return ""
    return value

def extract_name(soup, email="", phone=""):
    # Prefer explicit business/organization metadata.
    selectors = [
        ('meta[property="og:site_name"]', "content"),
        ('meta[property="og:title"]', "content"),
        ('meta[name="application-name"]', "content"),
        ("h1", None),
        ("title", None),
    ]
    for selector, attr in selectors:
        node = soup.select_one(selector)
        if node:
            text = node.get(attr, "") if attr else node.get_text(" ", strip=True)
            text = clean_text(text)
            if text:
                text = re.sub(r"\s*[|•–—]\s*(home|contact|about|official site).*$", "", text, flags=re.I)
                if 2 <= len(text) <= 120:
                    return text

    # Use LocalBusiness/Organization structured data when available.
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = script.string or script.get_text()
        if not raw:
            continue
        for key in ('"name"', '"legalName"'):
            m = re.search(key + r'\s*:\s*"([^"]{2,120})"', raw, re.I)
            if m:
                return clean_text(m.group(1))
    return ""

def extract_contacts(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    # Remove non-content noise.
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    emails = []
    phones = []

    # mailto/tel links are the highest-confidence fields.
    for a in BeautifulSoup(html, "html.parser").find_all("a"):
        href = (a.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            e = valid_email(href[7:].split("?", 1)[0])
            if e and e.lower() not in {x.lower() for x in emails}:
                emails.append(e)
        elif href.lower().startswith("tel:"):
            p = normalize_phone(href[4:].split("?", 1)[0])
            if p and p not in phones:
                phones.append(p)

    text = soup.get_text(" ", strip=True)
    for e in EMAIL_RE.findall(text):
        e = valid_email(e)
        if e and e.lower() not in {x.lower() for x in emails}:
            emails.append(e)

    for p in PHONE_RE.findall(text):
        p = normalize_phone(p)
        if p and p not in phones:
            phones.append(p)

    name = extract_name(soup, emails[0] if emails else "", phones[0] if phones else "")
    return {
        "full_name": name,
        "phone_number": phones[0] if phones else "",
        "email": emails[0] if emails else "",
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

def import_url(url, timeout=20):
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter a complete http:// or https:// URL.")
    _reject_private_host(parsed.hostname)

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    final_host = urlparse(response.url).hostname
    _reject_private_host(final_host)

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise ValueError("The URL did not return an HTML page.")

    record = extract_contacts(response.text, response.url)
    record["source_url"] = response.url
    record["http_status"] = response.status_code
    return record
