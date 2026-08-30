#!/usr/bin/env python3
"""
ChildDiary media downloader
===========================

Downloads all photos and videos referenced by ChildDiary JSON API responses,
filtered by date (default: from 2025-09-01 onwards).

No third-party dependencies - standard library only.

--------------------------------------------------------------------------
QUICK START
--------------------------------------------------------------------------
The easiest route is a HAR export (see README.md, in Portuguese):

1. Log in at https://app.childdiary.net, open the gallery, and scroll through
   all of it with DevTools -> Network open.

2. Right-click the request list -> "Save all as HAR with content".
   Save it as, e.g., galeria.har

3. Inspect what the script finds (no downloading):

       python3 childdiary_download.py galeria.har --list

4. Download:

       python3 childdiary_download.py galeria.har -o fotos

   A cookie is only needed if the media URLs require you to be logged in:

       python3 childdiary_download.py galeria.har --cookie "PASTE_COOKIE_HEADER"

You can also pass a folder of raw JSON responses instead of the .har file.

--------------------------------------------------------------------------
"""

import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

#

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://app.childdiary.net"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".3gp", ".mkv", ".webm", ".mpg", ".mpeg", ".wmv"}
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT

# JSON keys whose *value* is likely a media URL / path
URL_KEY_HINT = re.compile(
    r"(url|uri|link|href|src|path|file|filename|photo|image|picture|foto|imagem|"
    r"video|media|thumb|thumbnail|attachment|anexo|resource|recurso|download|blob)",
    re.I,
)

# JSON keys whose value is likely a timestamp
DATE_KEY_HINT = re.compile(
    r"(date|data|time|timestamp|created|criado|updated|atualizado|inserted|"
    r"registo|registro|dia|day|when|moment|publish|publicad)",
    re.I,
)

# Thumbnail-ish markers we prefer to skip when a full-size sibling exists
THUMB_HINT = re.compile(r"(thumb|thumbnail|small|preview|miniatur|_s\.|_t\.|/s/|=s\d+)", re.I)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# --------------------------------------------------------------------------
# Date parsing
# --------------------------------------------------------------------------

DATE_PATTERNS = [
    "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y",
]

# yyyymmdd or yyyy-mm-dd embedded in a filename/URL
DATE_IN_TEXT = re.compile(r"(20\d{2})[-_/]?(0[1-9]|1[0-2])[-_/]?(0[1-9]|[12]\d|3[01])")


def parse_date(value, dayfirst=True):
    """Best-effort conversion of a JSON value into a datetime.date. None if not a date."""
    if value is None or isinstance(value, bool):
        return None

    # Epoch numbers (seconds or milliseconds)
    if isinstance(value, (int, float)):
        n = float(value)
        if n > 1e17 or n <= 0:
            return None
        if n > 1e11:          # milliseconds
            n /= 1000.0
        if n > 1e10:          # microseconds -> already divided once, guard again
            n /= 1000.0
        if not (9e8 < n < 4e9):   # ~2000-01-01 .. ~2096
            return None
        try:
            return dt.datetime.fromtimestamp(n).date()
        except (OverflowError, OSError, ValueError):
            return None

    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s or len(s) > 40:
        return None

    # numeric string epoch
    if re.fullmatch(r"\d{10,13}", s):
        return parse_date(int(s))

    txt = s.replace("Z", "+0000")
    # strip colon in timezone offset (+01:00 -> +0100) for %z on older versions
    txt = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", txt)
    for fmt in DATE_PATTERNS:
        try:
            return dt.datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    # ambiguous d/m/Y vs m/d/Y already covered above; last resort: embedded date
    m = DATE_IN_TEXT.search(s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def date_from_url(url):
    m = DATE_IN_TEXT.search(url)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# JSON walking
# --------------------------------------------------------------------------

def looks_like_media(value):
    """Return True if the string value looks like a URL/path pointing at media."""
    if not isinstance(value, str):
        return False
    s = value.strip()
    if len(s) < 5 or len(s) > 2000:
        return False
    if s.startswith("data:"):
        return False
    if not (s.startswith("http://") or s.startswith("https://")
            or s.startswith("/") or s.startswith("./")
            or re.match(r"^[\w./-]+\.\w{2,5}(\?|$)", s)):
        return False
    path = urllib.parse.urlparse(s).path
    ext = os.path.splitext(path)[1].lower()
    if ext in MEDIA_EXT:
        return True
    # Extension-less resource endpoints, e.g. /api/resource/12345 or ?file=abc
    if re.search(r"(resource|media|photo|image|foto|imagem|video|file|attachment|"
                 r"anexo|download|blob|content)/[\w%-]{4,}", s, re.I):
        return True
    return False


def guess_kind(url, content_type=None):
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    if ext in IMAGE_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    if content_type:
        if content_type.startswith("image/"):
            return "photo"
        if content_type.startswith("video/"):
            return "video"
    if re.search(r"video", url, re.I):
        return "video"
    if re.search(r"(photo|image|foto|imagem|pic)", url, re.I):
        return "photo"
    return "unknown"


def walk(node, ancestors, out):
    """Recursively collect (url, date, context) tuples from arbitrary JSON."""
    if isinstance(node, dict):
        chain = ancestors + [node]
        # Any date living on this object or its ancestors (nearest wins)
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                walk(value, chain, out)
            elif looks_like_media(value) and (URL_KEY_HINT.search(key) or
                                              os.path.splitext(
                                                  urllib.parse.urlparse(value).path
                                              )[1].lower() in MEDIA_EXT):
                out.append({
                    "url": value.strip(),
                    "key": key,
                    "date": nearest_date(chain, value),
                    "title": nearest_title(chain),
                })
    elif isinstance(node, list):
        for item in node:
            walk(item, ancestors, out)
    elif isinstance(node, str) and looks_like_media(node):
        out.append({"url": node.strip(), "key": "", "date": nearest_date(ancestors, node),
                    "title": nearest_title(ancestors)})


def nearest_date(chain, url_value):
    """Walk from the closest object outwards looking for a plausible date."""
    for obj in reversed(chain):
        best = None
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                continue
            if not DATE_KEY_HINT.search(key):
                continue
            d = parse_date(value)
            if d and (best is None or d > best):
                best = d
        if best:
            return best
    # fall back to anything date-shaped in the URL itself
    return date_from_url(url_value)


def nearest_title(chain):
    for obj in reversed(chain):
        for key in ("title", "titulo", "name", "nome", "description", "descricao",
                    "descrição", "text", "texto", "caption", "legenda", "child",
                    "crianca", "criança"):
            v = obj.get(key)
            if isinstance(v, str) and 0 < len(v.strip()) <= 80:
                return v.strip()
    return ""


# --------------------------------------------------------------------------
# Collecting inputs
# --------------------------------------------------------------------------

def load_json_files(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                for n in sorted(names):
                    if n.lower().endswith((".json", ".txt", ".har")):
                        files.append(os.path.join(root, n))
        elif os.path.isfile(p):
            files.append(p)
        else:
            print(f"  ! not found: {p}", file=sys.stderr)
    return files


def extract_from_file(path):
    raw = open(path, "r", encoding="utf-8", errors="replace").read().strip()
    if not raw:
        return []
    docs = []
    try:
        docs.append(json.loads(raw))
    except json.JSONDecodeError:
        # NDJSON / concatenated objects / JS-wrapped response
        for line in raw.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            try:
                docs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not docs:
            print(f"  ! could not parse JSON: {path}", file=sys.stderr)
            return []

    found = []
    for doc in docs:
        # HAR file support: pull out every JSON response body
        if isinstance(doc, dict) and "log" in doc and isinstance(doc["log"], dict):
            for entry in doc["log"].get("entries", []):
                content = entry.get("response", {}).get("content", {})
                body = content.get("text")
                if not body:
                    continue
                if content.get("encoding") == "base64":
                    try:
                        body = base64.b64decode(body).decode("utf-8", "replace")
                    except ValueError:
                        continue
                try:
                    walk(json.loads(body), [], found)
                except json.JSONDecodeError:
                    continue
            continue
        walk(doc, [], found)
    return found


# --------------------------------------------------------------------------
# Downloading
# --------------------------------------------------------------------------

def absolutize(url, base):
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urllib.parse.urljoin(base.rstrip("/") + "/", url.lstrip("./"))


def safe_name(text, maxlen=60):
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"\s+", "_", text)
    return text[:maxlen].strip("._-")


def build_filename(item, url, content_type=None):
    parsed = urllib.parse.urlparse(url)
    base = os.path.basename(parsed.path) or "media"
    stem, ext = os.path.splitext(base)
    if not ext or ext.lower() not in MEDIA_EXT:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip()) if content_type else None
        ext = guessed or (".mp4" if item["kind"] == "video" else ".jpg")
        if ext == ".jpe":
            ext = ".jpg"
    stem = safe_name(stem) or "media"
    prefix = item["date"].isoformat() if item["date"] else "nodate"
    tail = hashlib.sha1(url.encode()).hexdigest()[:6]
    title = safe_name(item.get("title", ""), 30)
    parts = [prefix] + ([title] if title else []) + [stem, tail]
    return "_".join(p for p in parts if p)[:120] + ext.lower()


def make_opener(cookie, extra_headers, referer):
    opener = urllib.request.build_opener()
    headers = [("User-Agent", USER_AGENT), ("Accept", "*/*")]
    if referer:
        headers.append(("Referer", referer))
    if cookie:
        headers.append(("Cookie", cookie))
    for h in extra_headers or []:
        if ":" in h:
            k, v = h.split(":", 1)
            headers.append((k.strip(), v.strip()))
    opener.addheaders = headers
    return opener


def download(opener, url, dest, retries=3, timeout=60):
    tmp = dest + ".part"
    for attempt in range(1, retries + 1):
        try:
            with opener.open(url, timeout=timeout) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if "text/html" in ctype.lower():
                    return False, "got HTML (login required or bad URL)", ctype
                total = 0
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        fh.write(chunk)
                        total += len(chunk)
            if total == 0:
                os.remove(tmp)
                return False, "empty response", ctype
            os.replace(tmp, dest)
            return True, total, ctype
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return False, f"HTTP {e.code} (auth needed)", ""
            if e.code == 404:
                return False, "HTTP 404", ""
            err = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            err = str(e)[:120]
        if attempt < retries:
            time.sleep(1.5 * attempt)
    if os.path.exists(tmp):
        os.remove(tmp)
    return False, err, ""


def peek_content_type(opener, url, timeout=30):
    try:
        req = urllib.request.Request(url, method="HEAD")
        with opener.open(req, timeout=timeout) as resp:
            return resp.headers.get("Content-Type", "")
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Download ChildDiary photos & videos listed in JSON API responses.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("inputs", nargs="+", help="JSON file(s), a folder of them, or a .har file")
    ap.add_argument("-o", "--out", default="childdiary_media", help="output folder")
    ap.add_argument("--since", default="2025-09-01", help="earliest date, YYYY-MM-DD (default 2025-09-01)")
    ap.add_argument("--until", default=None, help="latest date, YYYY-MM-DD")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="base for relative URLs")
    ap.add_argument("--cookie", default=None, help='Cookie header, e.g. "JSESSIONID=...; other=..."')
    ap.add_argument("--cookie-file", default=None, help="file containing the Cookie header")
    ap.add_argument("-H", "--header", action="append", help='extra header "Name: value" (repeatable)')
    ap.add_argument("--referer", default=DEFAULT_BASE_URL + "/main", help="Referer header")
    ap.add_argument("--flat", action="store_true", help="all files in one folder (default: YYYY-MM subfolders)")
    ap.add_argument("--keep-thumbs", action="store_true", help="also download thumbnail-looking URLs")
    ap.add_argument("--no-date-only", action="store_true",
                    help="skip items with no detectable date (default: keep them)")
    ap.add_argument("--list", action="store_true", help="show what would be downloaded, download nothing")
    ap.add_argument("--delay", type=float, default=0.3, help="seconds between downloads")
    args = ap.parse_args()

    since = dt.date.fromisoformat(args.since)
    until = dt.date.fromisoformat(args.until) if args.until else None

    cookie = args.cookie
    if args.cookie_file:
        cookie = open(args.cookie_file, encoding="utf-8").read().strip()

    files = load_json_files(args.inputs)
    if not files:
        sys.exit("No JSON files found.")
    print(f"Reading {len(files)} file(s)...")

    raw_items = []
    for f in files:
        got = extract_from_file(f)
        print(f"  {os.path.basename(f)}: {len(got)} candidate URL(s)")
        raw_items.extend(got)

    # Normalise, dedupe, classify
    seen, items = set(), []
    for it in raw_items:
        url = absolutize(it["url"], args.base_url)
        if url in seen:
            continue
        seen.add(url)
        it["url"] = url
        it["kind"] = guess_kind(url)
        items.append(it)

    print(f"\n{len(items)} unique media URL(s) found.")

    # Filters
    kept, skipped_thumb, skipped_date, skipped_nodate = [], 0, 0, 0
    for it in items:
        if not args.keep_thumbs and THUMB_HINT.search(it["url"]):
            skipped_thumb += 1
            continue
        d = it["date"]
        if d is None:
            if args.no_date_only:
                skipped_nodate += 1
                continue
        else:
            if d < since or (until and d > until):
                skipped_date += 1
                continue
        kept.append(it)

    kept.sort(key=lambda x: (x["date"] or dt.date(1900, 1, 1), x["url"]))

    print(f"  {skipped_thumb} skipped as thumbnails (use --keep-thumbs to include)")
    print(f"  {skipped_date} outside the date range")
    if args.no_date_only:
        print(f"  {skipped_nodate} skipped with no detectable date")
    print(f"  -> {len(kept)} to download "
          f"({sum(1 for i in kept if i['kind'] == 'photo')} photos, "
          f"{sum(1 for i in kept if i['kind'] == 'video')} videos, "
          f"{sum(1 for i in kept if i['kind'] == 'unknown')} unknown)\n")

    if args.list:
        for it in kept[:400]:
            print(f"  {it['date'] or '????-??-??'}  {it['kind']:7s}  {it['url'][:120]}")
        if len(kept) > 400:
            print(f"  ... and {len(kept) - 400} more")
        return

    if not kept:
        print("Nothing to download. Run with --list to inspect, and check --since.")
        return

    opener = make_opener(cookie, args.header, args.referer)
    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, "manifest.csv")
    new_manifest = not os.path.exists(manifest_path)

    ok = failed = existing = 0
    with open(manifest_path, "a", newline="", encoding="utf-8") as mf:
        writer = csv.writer(mf)
        if new_manifest:
            writer.writerow(["date", "kind", "file", "bytes", "status", "url", "title"])

        for i, it in enumerate(kept, 1):
            folder = args.out if args.flat else os.path.join(
                args.out, it["date"].strftime("%Y-%m") if it["date"] else "no-date")
            os.makedirs(folder, exist_ok=True)

            ctype = None
            if os.path.splitext(urllib.parse.urlparse(it["url"]).path)[1].lower() not in MEDIA_EXT:
                ctype = peek_content_type(opener, it["url"])
                it["kind"] = guess_kind(it["url"], ctype)

            name = build_filename(it, it["url"], ctype)
            dest = os.path.join(folder, name)

            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                existing += 1
                print(f"[{i}/{len(kept)}] = {name} (already there)")
                continue

            good, info, ctype2 = download(opener, it["url"], dest)
            if good:
                ok += 1
                print(f"[{i}/{len(kept)}] + {name}  ({info/1024:.0f} KB)")
                writer.writerow([it["date"] or "", it["kind"], os.path.relpath(dest, args.out),
                                 info, "ok", it["url"], it.get("title", "")])
            else:
                failed += 1
                print(f"[{i}/{len(kept)}] ! {name}  -> {info}")
                writer.writerow([it["date"] or "", it["kind"], "", 0, f"failed: {info}",
                                 it["url"], it.get("title", "")])
            mf.flush()
            time.sleep(args.delay)

    print(f"\nDone. {ok} downloaded, {existing} already present, {failed} failed.")
    print(f"Files in: {os.path.abspath(args.out)}")
    print(f"Log:      {manifest_path}")
    if failed:
        print("\nIf failures say 'auth needed' or 'got HTML', re-run with a fresh --cookie.")


if __name__ == "__main__":
    main()

# --------------------------------------------------------------------------
# GETTING THE JSON  (and the cookie)
# --------------------------------------------------------------------------
#
# A. From the browser Network tab (simplest)
#    1. Log in at https://app.childdiary.net, open the gallery.
#    2. F12 -> Network -> filter "Fetch/XHR".
#    3. Scroll the gallery / click "Ver mais..." until everything back to
#       September 2025 has loaded. Each click fires one API request.
#    4. For each request that returns the gallery data: right-click ->
#       Copy -> Copy response, and paste into ./json/page1.json, page2.json, ...
#       (Or right-click in the Network list -> "Save all as HAR with content"
#        and pass that .har file straight to this script.)
#    5. The cookie: right-click the same request -> Copy -> Copy as cURL,
#       and take the value after -H 'Cookie: '.
#
# B. Straight from the page console (if the API is a simple paged endpoint)
#    Paste something like this in the DevTools console, adjusting the URL to
#    the one you saw in the Network tab, then save the downloaded file:
#
#      const all = [];
#      for (let page = 0; page < 50; page++) {
#        const r = await fetch(`/api/gallery?page=${page}&size=50`,
#                              {credentials: 'include'});
#        if (!r.ok) break;
#        const j = await r.json();
#        all.push(j);
#        const n = (j.content || j.items || j.data || j).length;
#        if (!n) break;
#      }
#      const blob = new Blob([JSON.stringify(all)], {type: 'application/json'});
#      const a = document.createElement('a');
#      a.href = URL.createObjectURL(blob);
#      a.download = 'childdiary.json';
#      a.click();
#
# --------------------------------------------------------------------------
