import os
import random
import re
import requests
import yt_dlp
import asyncio
import subprocess
import json
import base64
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="VidMax HD Backend")

# Enable CORS for Android client requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load Webshare proxies from Render Environment Variables
RAW_PROXIES = os.getenv("PROXY_LIST", "")
PROXY_POOL = [p.strip() for p in RAW_PROXIES.split(",") if p.strip()]

# Path to temporary cookies file
COOKIE_PATH = "/tmp/youtube_cookies.txt"

# Decode cookies from Render Environment Variable if present
def setup_cookies():
    cookies_b64 = os.getenv("YOUTUBE_COOKIES_B64")
    if cookies_b64:
        try:
            # Added .strip() to handle potential whitespace in environment variables
            decoded = base64.b64decode(cookies_b64.strip()).decode("utf-8")
            with open(COOKIE_PATH, "w", encoding="utf-8") as f:
                f.write(decoded)
            print("YouTube cookies loaded successfully.")
        except Exception as e:
            print(f"Failed to load cookies: {e}")

# Run setup at startup
setup_cookies()

def get_random_proxy() -> Optional[str]:
    """Selects a random Webshare proxy from the pool."""
    if not PROXY_POOL:
        return None
    return random.choice(PROXY_POOL)

def clean_input_url(url: str) -> str:
    """Strips accidental leading text or spaces from user input."""
    cleaned = url.strip()
    if "http" in cleaned and not cleaned.startswith("http"):
        cleaned = "http" + cleaned.split("http", 1)[1]
    return cleaned

def get_full_url(url: str) -> str:
    """Resolves short links like vt.tiktok.com or youtu.be to full URLs."""
    if "tiktok.com" in url or "youtu.be" in url:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            res = requests.head(url, allow_redirects=True, headers=headers, timeout=5)
            return res.url
        except Exception:
            return url
    return url


# --- ENGINE 1: PIPED YOUTUBE FALLBACK ENGINE ---
def extract_youtube_piped(target_url: str) -> Optional[Dict[str, Any]]:
    """Extracts YouTube media via Piped API instances when yt-dlp faces bot checks."""
    match = re.search(r'(?:v=|\/shorts\/|youtu\.be\/)([a-zA-Z0-9_-]{11})', target_url)
    if not match:
        return None

    video_id = match.group(1)
    piped_instances = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.adminforge.de",
        "https://pipedapi.tokhmi.xyz",
        "https://pipedapi.moomoo.me",
        "https://pipedapi.leptons.xyz",
        "https://piped-api.privacy.com.de"
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for instance in piped_instances:
        try:
            res = requests.get(f"{instance}/streams/{video_id}", headers=headers, timeout=6)
            if res.status_code == 200:
                data = res.json()
                qualities = []
                seen_res = set()

                for stream in data.get("videoStreams", []):
                    quality_label = stream.get("quality") or f"{stream.get('height')}p"
                    if stream.get("url") and quality_label not in seen_res:
                        seen_res.add(quality_label)
                        qualities.append({
                            "quality": quality_label,
                            "type": "video",
                            "extension": "mp4",
                            "download_url": stream.get("url"),
                            "size_bytes": stream.get("bitrate")
                        })

                for audio in data.get("audioStreams", []):
                    if audio.get("url") and "audio" not in seen_res:
                        seen_res.add("audio")
                        qualities.append({
                            "quality": "Audio Only",
                            "type": "audio",
                            "extension": "m4a",
                            "download_url": audio.get("url"),
                            "size_bytes": audio.get("bitrate")
                        })
                        break

                if qualities:
                    return {
                        "success": True,
                        "title": data.get("title", "YouTube Video"),
                        "thumbnail": data.get("thumbnailUrl"),
                        "duration": data.get("duration", 0),
                        "platform": "YouTube",
                        "qualities": qualities
                    }
        except Exception:
            continue

    return None


# --- ENGINE 2: YT-DLP ENGINE WITH MOBILE CLIENT SPOOFING ---
def extract_via_ytdlp(target_url: str, proxy_url: Optional[str]) -> Dict[str, Any]:
    """Robust yt-dlp extractor with mobile client spoofing and local binary support."""
    # Check multiple cookie locations
    cookie_paths = [COOKIE_PATH, "/etc/secrets/cookies.txt", "youtube_cookies.txt"]
    selected_cookie = next((path for path in cookie_paths if os.path.exists(path)), None)

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    # Check for local binary as requested to bypass datacenter blocks
    local_binary = "./yt-dlp"
    if os.path.exists(local_binary):
        cmd = [local_binary, "-j", "--no-warnings", "--user-agent", user_agent,
               "--add-header", "Accept-Language:en-US,en;q=0.9",
               "--add-header", "Referer:https://www.tiktok.com/",
               "--extractor-args", "youtube:player_client=android,ios,mweb"]
        if selected_cookie:
            cmd.extend(["--cookies", selected_cookie])
        if proxy_url:
            cmd.extend(["--proxy", proxy_url])
        cmd.append(target_url)

        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode != 0:
            raise Exception(f"Local binary extraction failed: {process.stderr}")
        info = json.loads(process.stdout)
    else:
        # Fallback to library engine
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'http_headers': {
                'User-Agent': user_agent,
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://www.tiktok.com/',
            },
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'mweb']
                }
            }
        }
        if proxy_url:
            ydl_opts['proxy'] = proxy_url
            ydl_opts['geo_verification_proxy'] = proxy_url
        if selected_cookie:
            ydl_opts['cookiefile'] = selected_cookie

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=False)

    qualities = []
    formats = info.get('formats', [])

    # Sort by resolution
    sorted_formats = sorted(formats, key=lambda x: (x.get('height') or 0), reverse=True)

    seen_res = set()
    for fmt in sorted_formats:
        format_url = fmt.get('url')
        if format_url:
            res = fmt.get('format_note') or fmt.get('resolution') or 'SD'
            if res not in seen_res:
                seen_res.add(res)
                qualities.append({
                    'quality': res,
                    'type': 'video' if fmt.get('vcodec') != 'none' else 'audio',
                    'extension': fmt.get('ext', 'mp4'),
                    'download_url': format_url,
                    'size_bytes': fmt.get('filesize') or fmt.get('filesize_approx')
                })

    return {
        "success": True,
        "title": info.get('title'),
        "thumbnail": info.get('thumbnail'),
        "duration": info.get('duration'),
        "platform": info.get('extractor'),
        "qualities": qualities
    }


# --- API ENDPOINTS ---

@app.get("/")
def home():
    return {
        "status": "online",
        "proxies_loaded": len(PROXY_POOL),
        "engine": "VidMax HD Hybrid Engine 3.7"
    }


@app.get("/download")
@app.get("/extract")
async def extract_media(url: str = Query(...)):
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")

    target_url = clean_input_url(url)
    target_url = get_full_url(target_url)
    selected_proxy = get_random_proxy()

    # 1. Try mobile-spoofed yt-dlp extraction (in worker thread)
    try:
        return await asyncio.to_thread(extract_via_ytdlp, target_url, selected_proxy)
    except Exception as primary_error:
        print(f"Primary extraction failed: {primary_error}")

        # 2. If YouTube bot check triggers, fallback to Piped API
        if "youtube.com" in target_url or "youtu.be" in target_url:
            print("Invoking Piped fallback for YouTube...")
            fallback_data = await asyncio.to_thread(extract_youtube_piped, target_url)
            if fallback_data:
                return fallback_data

        raise HTTPException(
            status_code=400,
            detail=f"Extraction failed: {str(primary_error)}"
        )


@app.get("/stream")
async def proxy_stream(stream_url: str = Query(...)):
    """Proxies direct video chunks through Webshare to bypass 403 blocks on client devices."""
    selected_proxy = get_random_proxy()
    proxies_dict = {"http": selected_proxy, "https": selected_proxy} if selected_proxy else None

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

    def iter_file():
        with requests.get(stream_url, headers=headers, proxies=proxies_dict, stream=True) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

    try:
        return StreamingResponse(iter_file(), media_type="video/mp4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stream proxy error: {str(e)}")
