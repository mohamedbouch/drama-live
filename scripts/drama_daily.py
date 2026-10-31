"""
Arabic sports channels -> Cloudflare Worker (key: drama_live_channels).
Runs once a day from GitHub Actions. Merges with what is already saved.

Environment:
  ADMIN_TOKEN  - Worker admin token (GitHub secret)
"""
import base64, json, os, re, sys, time
from curl_cffi import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

WORKER_KV = "https://mhd-api.mhdgimer.workers.dev/admin/kv?k=drama_live_channels"
TOPIC = os.environ.get("TOPIC", "arabic_sport")
TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

KEY = b"0123456789abcdef"
IV = b"fedcba9876543210"


def encrypt(data):
    ct = AES.new(KEY, AES.MODE_CBC, IV).encrypt(pad(data.encode(), 16))
    return base64.b64encode(ct).decode() + ":" + base64.b64encode(IV).decode()


def decrypt(enc):
    p = enc.split(":")
    return unpad(AES.new(KEY, AES.MODE_CBC, base64.b64decode(p[1])).decrypt(base64.b64decode(p[0])), 16).decode()


def api_call(endpoint, payload, host="live.publicdataroute.cc", is_redirect=False):
    enc = encrypt(json.dumps(payload, separators=(",", ":")))
    url = f"https://{host}/redirect/{endpoint}" if is_redirect else f"https://{host}/api/live/livedrama/v13.0.0/{endpoint}"
    headers = {"Content-Type": "application/json; charset=utf-8",
               "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; M2007J20CG MIUI/V12.0.9.0.QJGMIXM)"}
    try:
        r = requests.post(url, data=enc, headers=headers, timeout=20, impersonate="chrome", verify=False)
        if r.status_code in [200, 201]:
            return json.loads(decrypt(r.text.strip()))
    except Exception:
        pass
    return None


def base_payload():
    return {
        "user_id": "_4244_1793052507021_notloggedin.com_dramalive3",
        "device_id": "6e38bfe9-fe7e-4baf-81ae-99d214928f27",
        "device_api": "29", "version_name": "185", "language": "ar",
        "timezone": "Africa/Casablanca", "device_type": "phone",
        "KEY_ACTIVATED_TYPE": "202122", "store": "playStore",
        "isStoreVersion": True, "isPremium": False,
        "isCoupon_active": False, "hideAds": False,
        "appCount": json.dumps({"adsFailed": 6, "adsLoaded": 0, "adsShowed": 0, "runCount": 14}),
        "mainServer": "http://main.backendcoreapi.com/api/live/livedrama/v13.0.0/",
    }


def resolve_redirect(redirect_url, ch_id):
    payload = base_payload()
    payload.update({"id": ch_id, "url": redirect_url, "agent": "redirect"})
    data = api_call("getLiveByRedirect", payload, "redirect.publicdataroute.cc", is_redirect=True)
    if data and data.get("result") == 0:
        try:
            return json.loads(data.get("data", {}).get("url", "")).get("url")
        except Exception:
            pass
    return None


def get_channel_data(channel_id):
    payload = base_payload()
    payload["id"] = channel_id
    data = api_call("getLiveAllStreamsById", payload)
    if data and data.get("result") == 0:
        return data.get("live", {})
    return None


# ═══════════ استبعاد قنوات beIN Sports من 1 إلى 9 ═══════════
EXCLUDED_BEIN_NAMES = {
    "bein s. 1", "bein s. 2", "bein s. 3", "bein s. 4", "bein s. 5",
    "bein s. 6", "bein s. 7", "bein s. 8", "bein s. 9",
    "bein sport 1", "bein sport 2", "bein sport 3", "bein sport 4", "bein sport 5",
    "bein sport 6", "bein sport 7", "bein sport 8", "bein sport 9",
    "beinsport 1", "beinsport 2", "beinsport 3", "beinsport 4", "beinsport 5",
    "beinsport 6", "beinsport 7", "beinsport 8", "beinsport 9",
}


def _norm(x):
    return re.sub(r"\s+", " ", str(x or "")).strip().lower()


def is_excluded_channel(name):
    """يتحقق إذا كانت القناة من قنوات beIN Sports 1-9 (المستبعدة)"""
    return _norm(name) in EXCLUDED_BEIN_NAMES


def check_url(url):
    try:
        r = requests.get(url, impersonate="chrome", timeout=8, verify=False, allow_redirects=True)
        if r.status_code == 200:
            c = r.content[:500]
            if (b"#EXTM3U" in c or b"#EXT-X" in c or b"<MPD" in c or b"<mpd" in c
                    or c.startswith(b"\x47") or b".ts" in r.text.lower()
                    or b"<html" in c or b"<!DOCTYPE" in c):
                return True
        return False
    except Exception:
        return False


def worker_get():
    try:
        r = requests.get(WORKER_KV, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=20)
        if r.status_code == 200:
            return r.json().get("v") or {}
    except Exception:
        pass
    return {}


def worker_put(data):
    r = requests.put(WORKER_KV,
                     headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                     data=json.dumps(data, ensure_ascii=False).encode("utf-8"), timeout=30)
    return r.status_code


def main():
    if not TOKEN:
        print("ADMIN_TOKEN missing")
        sys.exit(1)

    print(f"Refreshing topic: {TOPIC}")
    existing = worker_get()
    print(f"Currently saved: {len(existing)}")

    resp = api_call("getLiveByTopic", {**base_payload(), "type": "tv", "topic": TOPIC})
    if not resp or "live" not in resp:
        print("Failed to fetch channel list. Nothing saved.")
        sys.exit(1)

    all_channels = resp["live"]
    
    # فلترة: استبعاد beIN 1-9
    channels = [c for c in all_channels if not is_excluded_channel(c.get("name", ""))]
    excluded_count = len(all_channels) - len(channels)
    
    print(f"Found {len(all_channels)} channels, excluded {excluded_count} (beIN 1-9), selected {len(channels)}")

    result = dict(existing)
    updated = 0

    for i, c in enumerate(channels, 1):
        ch_id, name = c.get("id_live"), c.get("name")
        logo = c.get("img_url", "")
        print(f"[{i}/{len(channels)}] {name}...", end=" ")
        
        live = get_channel_data(ch_id)
        if not live:
            print("no data (kept old)")
            continue
        
        text = (live.get("url", "") or "") + " " + (live.get("backup", "") or "")
        links = list(dict.fromkeys(re.findall(r"https?://[^\s\"\x27\\]+\.(?:m3u8|mpd)[^\s\"\x27\\]*", text)))
        for r_url in re.findall(r"http://\.LS\.V2[^\s\"\x27\\]+/s", text):
            res = resolve_redirect(r_url, ch_id)
            if res and res not in links:
                links.append(res)
        
        ok = [l for l in links if check_url(l)]
        
        if ok:
            print(f"ok {len(ok)}/{len(links)}")
            result[ch_id] = {"id": ch_id, "name": name, "logo": logo, "servers": ok,
                             "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            updated += 1
        else:
            print("no working link (kept old)")
        time.sleep(0.2)

    if updated == 0:
        print("No channel updated. Nothing saved.")
        sys.exit(1)

    status = worker_put(result)
    if status != 200:
        print(f"Save failed: {status}")
        sys.exit(1)
    
    print(f"Saved. Updated {updated}, total {len(result)}")


if __name__ == "__main__":
    main()
