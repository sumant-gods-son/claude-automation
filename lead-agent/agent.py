"""
GreenTec Lead Agent - agent.py
Finds US & UK businesses with no chat/voice widget and pushes them to GoHighLevel.

Leads are sourced 50/50 from the United States and the United Kingdom, and every
lead carries a normalized business website URL whenever one is available.

NOTE: the previous version of this file accidentally contained its whole body
twice. It has been consolidated into a single copy. The Google -> qualify ->
GoHighLevel workflow, auth, env vars, logging and scheduling are unchanged.
"""
import os, time, json, random, logging, re, requests
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from serpapi import GoogleSearch

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SERPAPI_KEY  = os.environ["SERPAPI_KEY"]
GHL_API_KEY  = os.environ["GHL_API_KEY"]
GHL_LOCATION = os.environ["GHL_LOCATION_ID"]

NICHES = [
    "plumber", "HVAC contractor", "dental clinic",
    "law firm", "real estate agent", "restaurant",
]

US_CITIES = [
    "New York NY", "Los Angeles CA", "Chicago IL", "Houston TX", "Phoenix AZ",
    "Philadelphia PA", "San Antonio TX", "San Diego CA", "Dallas TX",
    "Jacksonville FL", "Austin TX", "Columbus OH", "Charlotte NC",
    "Seattle WA", "Denver CO", "Nashville TN", "Portland OR",
    "Las Vegas NV", "Miami FL", "Atlanta GA",
]

UK_CITIES = [
    "London", "Birmingham", "Manchester", "Leeds", "Glasgow",
    "Liverpool", "Bristol", "Sheffield", "Edinburgh", "Cardiff",
    "Leicester", "Nottingham", "Newcastle upon Tyne", "Southampton",
    "Brighton", "Reading", "Coventry", "Belfast", "Bradford", "Portsmouth",
]

# Country routing. Combining SerpAPI's gl (geo-location) with an explicit
# country term in the query keeps results genuinely in-country - country is
# NOT inferred from a phone number or domain.
COUNTRIES = {
    "US": {"label": "United States", "gl": "us", "code": "US",
           "region": "USA",            "cities": US_CITIES},
    "UK": {"label": "United Kingdom", "gl": "uk", "code": "GB",
           "region": "United Kingdom", "cities": UK_CITIES},
}

CHAT_SIGNALS = [
    "intercom", "drift.com", "tidio", "livechat", "crisp.chat", "zendesk",
    "freshchat", "tawk.to", "olark", "hubspot", "smartsupp", "zopim",
    "purechat", "chatra", "userlike", "gorgias", "helpscout",
    "voiceflow", "bland.ai", "vapi.ai", "retell", "synthflow",
    "chat-widget", "chat_widget", "chatbot", "live-chat",
    "window.Tawk_API", "window.$crisp", "window.HubSpotConversations",
    "leadconnectorhq", "msgsndr",
]

HEADERS = {"User-Agent": (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)}

GHL_BASE = "https://services.leadconnectorhq.com"

# Query params that are tracking-only and safe to strip during normalization.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_reader", "utm_name", "gclid", "gclsrc", "dclid", "fbclid",
    "msclkid", "mc_cid", "mc_eid", "yclid", "_ga", "_gl", "ref", "ref_src",
    "igshid", "wickedid", "hsa_acc", "hsa_cam", "hsa_grp", "hsa_ad",
}


# ============================================================
# WEBSITE URL HELPERS
# ============================================================
def normalize_url(url):
    """Return a clean, canonical https URL, or '' if unusable.

    - adds https:// when a scheme is missing
    - forces http -> https
    - lowercases the host
    - drops tracking query params and any #fragment
    - strips a bare trailing slash
    """
    if not url:
        return ""
    url = url.strip()
    if not url:
        return ""
    if "://" in url:
        if not re.match(r"^https?://", url, re.I):
            return ""            # non-web scheme (ftp://, etc.)
    elif re.match(r"^(mailto|tel|javascript|data):", url, re.I):
        return ""                # not a website
    else:
        url = "https://" + url.lstrip("/")
    try:
        parts = urlsplit(url)
    except Exception:
        return ""
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return ""
    scheme = "https"
    host = parts.netloc.lower()
    if not host:
        return ""
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() not in TRACKING_PARAMS]
    query = urlencode(kept)
    path = parts.path
    if path == "/":
        path = ""
    return urlunsplit((scheme, host, path, query, ""))


def url_key(url):
    """Canonical dedup key so http/https, www. and trailing-slash variants of
    the same site collapse to one value."""
    if not url:
        return ""
    p = urlsplit(url if "://" in url else "https://" + url)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host + p.path.rstrip("/")


# Standard-format check mirroring GoHighLevel's email validator, so we never
# post a malformed scraped string (which makes GHL reject the WHOLE contact).
EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}$")

def is_valid_email(email):
    if not email:
        return False
    email = email.strip().lower()
    if len(email) > 254 or ".." in email or email.startswith(".") or "@." in email or ".@" in email:
        return False
    return bool(EMAIL_RE.match(email))


# ============================================================
# SERPAPI SEARCH
# ============================================================
def fetch_website_from_place(place_id, gl="us"):
    """Priority 2: pull the website from the Google Business Profile via the
    Maps place-details endpoint."""
    try:
        params = {
            "api_key": SERPAPI_KEY,
            "engine": "google_maps",
            "type": "place",
            "place_id": place_id,
            "gl": gl,
        }
        result = GoogleSearch(params).get_dict()
        return result.get("place_results", {}).get("website", "")
    except Exception:
        return ""


def search_website_google(business_name, city, region, gl="us"):
    """Priority 3: last-resort organic Google search for the official site,
    skipping directories/aggregators."""
    if not business_name:
        return ""
    try:
        params = {
            "api_key": SERPAPI_KEY,
            "engine": "google",
            "q": f'"{business_name}" {city} {region} official website',
            "num": 3,
            "gl": gl,
        }
        results = GoogleSearch(params).get_dict()
        skip = ["yelp.com", "yellowpages", "facebook.com", "google.com",
                "bbb.org", "angi.com", "thumbtack", "tripadvisor",
                "linkedin.com", "instagram.com", "yell.com"]
        for r in results.get("organic_results", []):
            link = r.get("link", "")
            if link and not any(s in link.lower() for s in skip):
                return link
        return ""
    except Exception:
        return ""


def resolve_website(place, city, cfg):
    """Website priority:
      1. Official business website listed on the Maps result (GBP website)
      2. Website from the Google Business Profile place-details lookup
      3. Website found via an organic Google search of the business listing
      4. '' when nothing is found
    The winning URL is normalized before it is returned.
    """
    raw = place.get("website", "") or place.get("links", {}).get("website", "")
    website = normalize_url(raw)
    if not website and place.get("place_id"):
        website = normalize_url(fetch_website_from_place(place["place_id"], cfg["gl"]))
    if not website:
        website = normalize_url(
            search_website_google(place.get("title", ""), city, cfg["region"], cfg["gl"]))
    return website


def search_businesses(niche, city, cfg):
    """Search one niche in one city, genuinely scoped to cfg's country."""
    params = {
        "api_key": SERPAPI_KEY, "engine": "google_maps",
        "q": f"{niche} in {city}, {cfg['region']}",
        "type": "search", "hl": "en", "gl": cfg["gl"],
    }
    try:
        results = GoogleSearch(params).get_dict()
        bizs = []
        for p in results.get("local_results", []):
            bizs.append({
                "name": p.get("title", ""), "address": p.get("address", ""),
                "phone": p.get("phone", ""),
                "website": resolve_website(p, city, cfg),
                "niche": niche, "city": city,
                "country": cfg["label"], "country_code": cfg["code"],
            })
        log.info(f"[{cfg['code']}] Found {len(bizs)} for '{niche}' in {city}")
        return bizs
    except Exception as e:
        log.error(f"SerpAPI error: {e}")
        return []


# ============================================================
# QUALIFICATION
# ============================================================
def has_chat_widget(url, timeout=8):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout,
                            allow_redirects=True)
        html = resp.text.lower()
        for s in CHAT_SIGNALS:
            if s.lower() in html:
                return True
        return False
    except Exception:
        return True


def extract_email(url):
    junk = ["noreply", "no-reply", "privacy", ".png", ".jpg", ".jpeg",
            ".gif", ".webp", ".svg"]
    for page in [url, url.rstrip("/") + "/contact"]:
        try:
            resp = requests.get(page, headers=HEADERS, timeout=6)
            found = re.findall(
                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}",
                resp.text)
            for e in found:
                e = e.strip().strip(".").lower()
                if any(x in e for x in junk):
                    continue
                if is_valid_email(e):
                    return e
        except Exception:
            pass
    return ""


# ============================================================
# GOHIGHLEVEL
# ============================================================
def create_ghl_contact(biz):
    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }
    payload = {
        "locationId":  GHL_LOCATION,
        "firstName":   biz.get("name", ""),
        "companyName": biz.get("name", ""),
        "phone":       biz.get("phone", ""),
        "website":     biz.get("website", ""),
        "address1":    biz.get("address", ""),
        "country":     biz.get("country_code", ""),
        "source":      "GreenTec Lead Agent",
        "tags": [
            "no-chat-lead",
            f"niche:{biz['niche'].replace(' ', '-')}",
            f"city:{biz['city'].replace(' ', '-')}",
            f"country:{biz.get('country_code', '')}",
            "source:lead-agent",
        ],
    }
    # Only attach an email GHL will accept; a bad email 422s the whole contact.
    email = biz.get("email", "")
    if is_valid_email(email):
        payload["email"] = email

    try:
        resp = requests.post(f"{GHL_BASE}/contacts/", headers=headers,
                             json=payload, timeout=10)
        # Safety net: if GHL still rejects on the email, drop it and retry so
        # the lead (with its website) is never lost over an email issue.
        if resp.status_code == 422 and "email" in payload and \
           "email" in resp.text.lower():
            log.warning(f"  GHL 422 on email '{payload['email']}' - retrying without email")
            payload.pop("email", None)
            resp = requests.post(f"{GHL_BASE}/contacts/", headers=headers,
                                 json=payload, timeout=10)
        if resp.status_code in (200, 201):
            cid = resp.json().get("contact", {}).get("id", "")
            log.info(f"  GHL contact: {biz['name']} [{cid}]")
            return resp.json()
        else:
            log.warning(f"  GHL {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"  GHL failed: {e}")
    return None


# ============================================================
# ORCHESTRATION
# ============================================================
def _split_quota(leads_per_run, codes):
    """Split the run target as evenly as possible across countries; any
    remainder is handed out at random so it stays balanced over many runs."""
    n = len(codes)
    base = leads_per_run // n
    quotas = {c: base for c in codes}
    for c in random.sample(codes, leads_per_run - base * n):
        quotas[c] += 1
    return quotas


def collect_for_country(cfg, quota, niches, seen, run_log, cities=None):
    """Push up to `quota` qualified, no-chat leads for one country. Works
    through many city x niche combinations, so if one search returns few
    results it keeps searching additional locations/categories in the same
    country until the quota is met or combinations are exhausted."""
    if quota <= 0:
        return 0
    city_pool = list(cities or cfg["cities"])
    combos = [(n, c) for c in city_pool for n in niches]
    random.shuffle(combos)

    got = 0
    for niche, city in combos:
        if got >= quota:
            break
        for biz in search_businesses(niche, city, cfg):
            if got >= quota:
                break
            website = biz.get("website", "")
            key = url_key(website)
            if key and key in seen:
                log.info(f"  Skipped - duplicate {website}")
                continue
            if not website:
                # No website means we cannot verify chat presence, so the
                # business cannot be qualified as a no-chat lead.
                log.info(f"  Skipped - no website | {biz['name']}")
                continue
            time.sleep(random.uniform(1.5, 3.5))
            log.info(f"[{cfg['code']}] Checking: {biz['name']} | {website}")
            if has_chat_widget(website):
                log.info("  Skipped - chat found")
                continue
            seen.add(key)
            biz["email"] = extract_email(website)
            if create_ghl_contact(biz):
                got += 1
                run_log.append({**biz, "status": "created",
                                "ts": datetime.utcnow().isoformat()})
        time.sleep(2)

    log.info(f"[{cfg['code']}] {cfg['label']}: {got}/{quota} leads pushed")
    return got


def run_agent(leads_per_run=50, cities=None, niches=None, countries=None):
    niches = niches or NICHES
    codes  = countries or list(COUNTRIES.keys())   # ["US", "UK"] -> 50/50
    quotas = _split_quota(leads_per_run, codes)

    total   = 0
    run_log = []
    seen    = set()
    by_country = {}
    started = datetime.utcnow().isoformat()
    log.info(f"=== Lead Agent started | {started} ===")
    log.info(f"Target {leads_per_run} | Quotas: {quotas} | Niches: {niches}")

    for code in codes:
        got = collect_for_country(COUNTRIES[code], quotas[code], niches,
                                  seen, run_log, cities)
        by_country[code] = got
        total += got

    log.info(f"=== Done: {total} leads pushed | by country: {by_country} ===")

    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    fname = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    fpath = os.path.join(log_dir, fname)
    with open(fpath, "w") as f:
        json.dump({"started": started, "total": total,
                   "by_country": by_country, "leads": run_log}, f, indent=2)
    log.info(f"Log: {fpath}")
    return {"started": started, "total": total,
            "by_country": by_country, "leads": run_log}


if __name__ == "__main__":
    run_agent()
