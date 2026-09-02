import asyncio
import time
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import yt_dlp

app = FastAPI(
    title="VidMax HD Extraction Engine",
    description="High-performance multi-platform video stream extraction API",
    version="2.0.0"
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


# --- SUPERSONIC URL RESOLVER ---
def resolve_share_url(url: str) -> str:
    """Resolves share links, redirects, and cleans tracking parameters."""
    shortener_domains = [
        "facebook.com/share", "fb.watch", "vt.tiktok.com",
        "vm.tiktok.com", "t.co", "youtu.be", "instagram.com/share"
    ]

    if any(domain in url for domain in shortener_domains):
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
            }
            res = requests.get(url, allow_redirects=True, timeout=8, headers=headers, stream=True)
            resolved = res.url

            # Clean TikTok tracking query parameters that break yt-dlp parsing
            if "tiktok.com" in resolved and "?" in resolved:
                resolved = resolved.split("?")[0]

            resolved = resolved.replace("?_fb_noscript=1", "").replace("&_fb_noscript=1", "")
            return resolved
        except Exception:
            pass
    return url


# --- CORE EXTRACTION ENGINE ---
def extract_media_sync(target_url: str) -> Dict[str, Any]:
    """Synchronous yt-dlp extractor to run inside thread pool."""
    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'allow_unplayable_formats': False,
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
    """Health check route for uptime monitors and fast cold-start wakeups."""
    return {"status": "online", "engine": "VidMax HD Supersonic 2.0"}


@app.get("/download")
@app.get("/extract")
async def extract_video(url: str = Query(..., description="Target video URL")):
    """Extracts direct video download links with multi-quality stream options."""
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required.")

    clean_target = resolve_share_url(url)

    # Return cached response instantly if available
    cached_data = get_from_cache(clean_target)
    if cached_data:
        cached_data["cached"] = True
        return cached_data

    try:
        # Offload heavy extraction to async worker thread
        data = await asyncio.to_thread(extract_media_sync, clean_target)
        data["cached"] = False

        # Save to cache
        set_to_cache(clean_target, data)
        return data

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")
