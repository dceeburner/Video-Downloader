import asyncio
import time
import re
import html
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import yt_dlp

app = FastAPI(
    title="VidMax HD Extraction Engine",
    description="High-performance multi-platform video stream extraction API",
    version="2.2.0"
)

# Enable CORS for Android client requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- IN-MEMORY TTL CACHE (30 Minute Expiration) ---
CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 1800  # seconds

def get_from_cache(url: str) -> Optional[Dict[str, Any]]:
    if url in CACHE:
        data, timestamp = CACHE[url]["data"], CACHE[url]["timestamp"]
        if time.time() - timestamp < CACHE_TTL:
            return data
        else:
            del CACHE[url]
    return None

def set_to_cache(url: str, data: Dict[str, Any]):
    CACHE[url] = {"data": data, "timestamp": time.time()}


# --- CUSTOM DIRECT FACEBOOK SCRAPER ---
def extract_facebook_direct(url: str) -> Optional[Dict[str, Any]]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    try:
        session = requests.Session()
        res = session.get(url, headers=headers, timeout=10, allow_redirects=True)
        html_content = res.text

        title_match = re.search(r'<title>(.*?)</title>', html_content)
        title = html.unescape(title_match.group(1)) if title_match else "Facebook Video"
        title = title.replace(" | Facebook", "").strip()

        thumb_match = re.search(r'property="og:image"\s+content="([^"]+)"', html_content)
        thumbnail = html.unescape(thumb_match.group(1)) if thumb_match else ""

        qualities = []
        hd_match = (
            re.search(r'"playable_url_quality_hd":"([^"]+)"', html_content) or
            re.search(r'hd_src:"([^"]+)"', html_content) or
            re.search(r'"browser_native_hd_url":"([^"]+)"', html_content)
        )
        sd_match = (
            re.search(r'"playable_url":"([^"]+)"', html_content) or
            re.search(r'sd_src:"([^"]+)"', html_content) or
            re.search(r'"browser_native_sd_url":"([^"]+)"', html_content) or
            re.search(r'property="og:video:secure_url"\s+content="([^"]+)"', html_content) or
            re.search(r'property="og:video"\s+content="([^"]+)"', html_content)
        )

        def clean_stream_url(raw_url: str) -> str:
            cleaned = raw_url.replace(r'\/', '/').replace(r'\u0025', '%')
            return html.unescape(cleaned)

        if hd_match:
            qualities.append({
                "quality": "1080p HD",
                "type": "video",
                "extension": "mp4",
                "size_bytes": None,
                "download_url": clean_stream_url(hd_match.group(1))
            })

        if sd_match:
            sd_url = clean_stream_url(sd_match.group(1))
            if not qualities or qualities[0]["download_url"] != sd_url:
                qualities.append({
                    "quality": "720p / 480p SD",
                    "type": "video",
                    "extension": "mp4",
                    "size_bytes": None,
                    "download_url": sd_url
                })

        if qualities:
            return {
                "success": True,
                "title": title,
                "thumbnail": thumbnail,
                "duration": 0,
                "platform": "Facebook",
                "qualities": qualities
            }
    except Exception:
        pass
    return None


# --- CORE EXTRACTION ENGINE ---
def extract_media_sync(target_url: str) -> Dict[str, Any]:
    # 1. Direct Scraper for Facebook links
    if "facebook.com" in target_url or "fb.watch" in target_url:
        fb_data = extract_facebook_direct(target_url)
        if fb_data:
            return fb_data

    # 2. General yt-dlp Extractor with YouTube Mobile/TV Client Spoofing
    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'allow_unplayable_formats': False,
        # Bypass YouTube's datacenter IP bot block on cloud servers
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb', 'tv_embedded']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target_url, download=False)
        clean_info = ydl.sanitize_info(info)

        qualities: List[Dict[str, Any]] = []
        seen_resolutions = set()

        raw_formats = clean_info.get("formats", [])

        for f in reversed(raw_formats):
            direct_url = f.get("url")
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            height = f.get("height")
            ext = f.get("ext", "mp4")

            if not direct_url:
                continue

            if height and height >= 144 and vcodec != "none":
                res_label = f"{height}p"
                if res_label not in seen_resolutions:
                    seen_resolutions.add(res_label)
                    qualities.append({
                        "quality": res_label,
                        "type": "video",
                        "extension": ext if ext != "m3u8" else "mp4",
                        "size_bytes": f.get("filesize") or f.get("filesize_approx"),
                        "download_url": direct_url
                    })

            elif vcodec == "none" and acodec != "none" and "audio" not in seen_resolutions:
                seen_resolutions.add("audio")
                qualities.append({
                    "quality": "Audio Only (MP3/M4A)",
                    "type": "audio",
                    "extension": ext,
                    "size_bytes": f.get("filesize") or f.get("filesize_approx"),
                    "download_url": direct_url
                })

        if not qualities and clean_info.get("url"):
            qualities.append({
                "quality": "HD Best Quality",
                "type": "video",
                "extension": clean_info.get("ext", "mp4"),
                "size_bytes": clean_info.get("filesize"),
                "download_url": clean_info.get("url")
            })

        return {
            "success": True,
            "title": clean_info.get("title", "Video"),
            "thumbnail": clean_info.get("thumbnail"),
            "duration": clean_info.get("duration", 0),
            "platform": clean_info.get("extractor_key", "Unknown"),
            "qualities": qualities
        }


# --- API ENDPOINTS ---

@app.get("/")
def health_check():
    return {"status": "online", "engine": "VidMax HD Engine 2.2"}


@app.get("/download")
@app.get("/extract")
async def extract_video(url: str = Query(..., description="Target video URL")):
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required.")

    cached_data = get_from_cache(url)
    if cached_data:
        cached_data["cached"] = True
        return cached_data

    try:
        data = await asyncio.to_thread(extract_media_sync, url)
        data["cached"] = False
        set_to_cache(url, data)
        return data
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")
