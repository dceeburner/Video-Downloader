import asyncio
import time
import re
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import yt_dlp

app = FastAPI(
    title="VidMax HD Extraction Engine",
    description="Multi-Engine Video Downloader Backend",
    version="3.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 1800  # 30 minutes

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


# --- ENGINE 1: PIPED YOUTUBE STREAM ENGINE (Bypasses Datacenter Bot Checks) ---
def extract_youtube_piped(target_url: str) -> Optional[Dict[str, Any]]:
    """Extracts YouTube streams via Piped API instances without requiring cookies."""
    # Extract 11-character YouTube Video ID
    video_id_match = re.search(r'(?:v=|\/shorts\/|youtu\.be\/)([a-zA-Z0-9_-]{11})', target_url)
    if not video_id_match:
        return None

    video_id = video_id_match.group(1)

    piped_instances = [
        "https://pipedapi.kavin.rocks",
        "https://api.piped.privacydev.net",
        "https://pipedapi.palvelu.org",
        "https://pipedapi.adminforge.de"
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    for instance in piped_instances:
        try:
            res = requests.get(f"{instance}/streams/{video_id}", headers=headers, timeout=6)
            if res.status_code == 200:
                data = res.json()
                qualities = []
                seen_res = set()

                # Process Video Streams
                for stream in data.get("videoStreams", []):
                    quality_label = stream.get("quality") or f"{stream.get('height')}p"
                    mime_type = stream.get("mimeType", "")

                    if stream.get("url") and quality_label not in seen_res:
                        seen_res.add(quality_label)
                        qualities.append({
                            "quality": quality_label,
                            "type": "video",
                            "extension": "mp4" if "mp4" in mime_type else "webm",
                            "size_bytes": stream.get("bitrate"),
                            "download_url": stream.get("url")
                        })

                # Process Audio Streams
                for audio in data.get("audioStreams", []):
                    if audio.get("url") and "audio" not in seen_res:
                        seen_res.add("audio")
                        qualities.append({
                            "quality": "Audio Only (MP3/M4A)",
                            "type": "audio",
                            "extension": "m4a",
                            "size_bytes": audio.get("bitrate"),
                            "download_url": audio.get("url")
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


# --- ENGINE 2: STANDARD YT-DLP ENGINE (TikTok, Instagram, Facebook, etc.) ---
def extract_via_yt_dlp(target_url: str) -> Dict[str, Any]:
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


# --- ROUTER CONTROLLER ---
def extract_media(target_url: str) -> Dict[str, Any]:
    # 1. Route YouTube links through YouTube Piped Engine
    if "youtube.com" in target_url or "youtu.be" in target_url:
        yt_data = extract_youtube_piped(target_url)
        if yt_data:
            return yt_data

    # 2. Route TikTok, Instagram, and other sites through yt-dlp
    return extract_via_yt_dlp(target_url)


# --- API ENDPOINTS ---

@app.get("/")
def health_check():
    return {"status": "online", "engine": "VidMax HD Multi-Engine 3.1"}


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
        data = await asyncio.to_thread(extract_media, url)
        data["cached"] = False
        set_to_cache(url, data)
        return data
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")
