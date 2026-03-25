"""
GreenTec Lead Agent - agent.py
Finds US businesses with no chat/voice widget and pushes them to GoHighLevel
"""
import os, time, json, random, logging, re, requests
from datetime import datetime
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


def fetch_website_from_place(place_id):
    try:
        params = {
            "api_key": SERPAPI_KEY,
            "engine": "google_maps",
            "type": "place",
            "place_id": place_id,
        }
        result = GoogleSearch(params).get_dict()
        return result.get("place_results", {}).get("website", "")
    except:
        return ""


def search_website_google(business_name, city):
    if not business_name:
        return ""
    try:
        params = {
            "api_key": SERPAPI_KEY,
            "engine": "google",
            "q": f'"{business_name}" {city} official website',
            "num": 3,
        }
        results = GoogleSearch(params).get_dict()
        skip = ["yelp.com","yellowpages","facebook.com","google.com",
                "bbb.org","angi.com","thumbtack","tripadvisor","linkedin.com"]
        for r in results.get("organic_results", []):
            link = r.get("link", "")
            if link and not any(s in link.lower() for s in skip):
                return link
        return ""
    except:
        return ""


def search_businesses(niche, city, num=10):
    params = {
        "api_key": SERPAPI_KEY, "engine": "google_maps",
        "q": f"{niche} in {city}",
        "type": "search", "hl": "en", "gl": "us",
    }
    try:
        results = GoogleSearch(params).get_dict()
        bizs = []
        for p in results.get("local_results", []):
            website = p.get("website", "") or p.get("links", {}).get("website", "")
            if not website and p.get("place_id"):
                website = fetch_website_from_place(p["place_id"])
            if not website:
                website = search_website_google(p.get("title", ""), city)
            biz = {
                "name": p.get("title",""), "address": p.get("address",""),
                "phone": p.get("phone",""), "website": website,
                "niche": niche, "city": city,
            }
            bizs.append(biz)
        log.info(f"Found {len(bizs)} for '{niche}' in {city}")
        return bizs
    except Exception as e:
        log.error(f"SerpAPI error: {e}")
        return []


def has_chat_widget(url, timeout=8):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout,
                            allow_redirects=True)
        html = resp.text.lower()
        for s in CHAT_SIGNALS:
            if s.lower() in html:
                return True
        return False
    except:
        return True


def extract_email(url):
    for page in [url, url.rstrip("/") + "/contact"]:
        try:
            resp = requests.get(page, headers=HEADERS, timeout=6)
            found = re.findall(
                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                resp.text)
            clean = [e for e in found if not any(x in e.lower()
                     for x in ["noreply","no-reply","privacy",".png",".jpg"])]
            if clean:
                return clean[0]
        except:
            pass
    return ""


def create_ghl_contact(biz):
    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }
    payload = {
        "locationId":  GHL_LOCATION,
        "firstName":   biz.get("name",""),
        "companyName": biz.get("name",""),
        "phone":       biz.get("phone",""),
        "email":       biz.get("email",""),
        "website":     biz.get("website",""),
        "address1":    biz.get("address",""),
        "source":      "GreenTec Lead Agent",
        "tags": [
            "no-chat-lead",
            f"niche:{biz['niche'].replace(' ','-')}",
            f"city:{biz['city'].replace(' ','-')}",
            "source:lead-agent",
        ],
    }
    try:
        resp = requests.post(f"{GHL_BASE}/contacts/", headers=headers,
                             json=payload, timeout=10)
        if resp.status_code in (200, 201):
            cid = resp.json().get("contact",{}).get("id","")
            log.info(f"  GHL contact: {biz['name']} [{cid}]")
            return resp.json()
        else:
            log.warning(f"  GHL {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"  GHL failed: {e}")
    return None


def run_agent(leads_per_run=50, cities=None, niches=None):
    cities  = cities  or random.sample(US_CITIES, 5)
    niches  = niches  or NICHES
    total   = 0
    run_log = []
    started = datetime.utcnow().isoformat()
    log.info(f"=== Lead Agent started | {started} ===")
    log.info(f"Cities: {cities} | Niches: {niches}")

    for niche in niches:
        for city in cities:
            if total >= leads_per_run:
                break
            for biz in search_businesses(niche, city):
                if total >= leads_per_run:
                    break
                time.sleep(random.uniform(1.5, 3.5))
                log.info(f"Checking: {biz['name']} | {biz['website']}")

                if has_chat_widget(biz["website"]):
                    log.info("  Skipped - chat found")
                    continue

                biz["email"] = extract_email(biz["website"])
                if create_ghl_contact(biz):
                    total += 1
                    run_log.append({**biz, "status":"created",
                                    "ts": datetime.utcnow().isoformat()})
            time.sleep(2)

    log.info(f"=== Done: {total} leads pushed ===")

    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    fname = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    fpath = os.path.join(log_dir, fname)
    with open(fpath, "w") as f:
        json.dump({"started": started, "total": total, "leads": run_log}, f, indent=2)
    log.info(f"Log: {fpath}")
    return {"started": started, "total": total, "leads": run_log}


if __name__ == "__main__":
    run_agent()
"""
GreenTec Lead Agent - agent.py
Finds US businesses with no chat/voice widget and pushes them to GoHighLevel
"""
import os, time, json, random, logging, re, requests
from datetime import datetime
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


def search_businesses(niche, city, num=10):
    params = {
        "api_key": SERPAPI_KEY, "engine": "google_maps",
        "q": f"{niche} in {city}",
        "type": "search", "hl": "en", "gl": "us",
    }
    try:
        results = GoogleSearch(params).get_dict()
        bizs = []
        for p in results.get("local_results", []):
            biz = {
                "name": p.get("title",""), "address": p.get("address",""),
                "phone": p.get("phone",""), "website": p.get("website",""),
                "niche": niche, "city": city,
            }
            if biz["website"]:
                bizs.append(biz)
        log.info(f"Found {len(bizs)} for '{niche}' in {city}")
        return bizs
    except Exception as e:
        log.error(f"SerpAPI error: {e}")
        return []


def has_chat_widget(url, timeout=8):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout,
                            allow_redirects=True)
        html = resp.text.lower()
        for s in CHAT_SIGNALS:
            if s.lower() in html:
                return True
        return False
    except:
        return True


def extract_email(url):
    for page in [url, url.rstrip("/") + "/contact"]:
        try:
            resp = requests.get(page, headers=HEADERS, timeout=6)
            found = re.findall(
                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                resp.text)
            clean = [e for e in found if not any(x in e.lower()
                     for x in ["noreply","no-reply","privacy",".png",".jpg"])]
            if clean:
                return clean[0]
        except:
            pass
    return ""


def create_ghl_contact(biz):
    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }
    payload = {
        "locationId":  GHL_LOCATION,
        "firstName":   biz.get("name",""),
        "companyName": biz.get("name",""),
        "phone":       biz.get("phone",""),
        "email":       biz.get("email",""),
        "website":     biz.get("website",""),
        "address1":    biz.get("address",""),
        "source":      "GreenTec Lead Agent",
        "tags": [
            "no-chat-lead",
            f"niche:{biz['niche'].replace(' ','-')}",
            f"city:{biz['city'].replace(' ','-')}",
            "source:lead-agent",
        ],
    }
    try:
        resp = requests.post(f"{GHL_BASE}/contacts/", headers=headers,
                             json=payload, timeout=10)
        if resp.status_code in (200, 201):
            cid = resp.json().get("contact",{}).get("id","")
            log.info(f"  GHL contact: {biz['name']} [{cid}]")
            return resp.json()
        else:
            log.warning(f"  GHL {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"  GHL failed: {e}")
    return None


def run_agent(leads_per_run=50, cities=None, niches=None):
    cities  = cities  or random.sample(US_CITIES, 5)
    niches  = niches  or NICHES
    total   = 0
    run_log = []
    started = datetime.utcnow().isoformat()
    log.info(f"=== Lead Agent started | {started} ===")
    log.info(f"Cities: {cities} | Niches: {niches}")

    for niche in niches:
        for city in cities:
            if total >= leads_per_run:
                break
            for biz in search_businesses(niche, city):
                if total >= leads_per_run:
                    break
                time.sleep(random.uniform(1.5, 3.5))
                log.info(f"Checking: {biz['name']} | {biz['website']}")

                if has_chat_widget(biz["website"]):
                    log.info("  Skipped - chat found")
                    continue

                biz["email"] = extract_email(biz["website"])
                if create_ghl_contact(biz):
                    total += 1
                    run_log.append({**biz, "status":"created",
                                    "ts": datetime.utcnow().isoformat()})
            time.sleep(2)

    log.info(f"=== Done: {total} leads pushed ===")

    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    fname = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    fpath = os.path.join(log_dir, fname)
    with open(fpath, "w") as f:
        json.dump({"started": started, "total": total, "leads": run_log}, f, indent=2)
    log.info(f"Log: {fpath}")
    return {"started": started, "total": total, "leads": run_log}


if __name__ == "__main__":
    run_agent()
