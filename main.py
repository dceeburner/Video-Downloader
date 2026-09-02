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
    description="Cascading Multi-Engine Video Downloader Backend",
    version="3.0.0"
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


# --- ENGINE 1: COBALT FALLBACK ENGINE (Handles Bot-Protected YouTube & Facebook Links) ---
def extract_via_cobalt_fallback(target_url: str) -> Optional[Dict[str, Any]]:
    """Primary/Fallback engine utilizing open-source processing nodes."""
    cobalt_instances = [
        "https://api.cobalt.tools/",
        "https://cobalt-api.kwiatekmom.pl/",
    ]

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"
    }

    payload = {
        "url": target_url,
        "videoQuality": "max",
        "youtubeVideoCodec": "h264",
        "audioFormat": "mp3"
    }

    for instance in cobalt_instances:
        try:
            res = requests.post(instance, json=payload, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                status = data.get("status")

                qualities = []
                if status in ["stream", "redirect"]:
                    download_url = data.get("url")
                    if download_url:
                        qualities.append({
                            "quality": "HD Best Quality",
                            "type": "video",
                            "extension": "mp4",
                            "size_bytes": None,
                            "download_url": download_url
                        })
                elif status == "picker":
                    for item in data.get("picker", []):
                        if item.get("type") in ["video", "photo"] and item.get("url"):
                            qualities.append({
                                "quality": "HD Media",
                                "type": item.get("type"),
                                "extension": "mp4" if item.get("type") == "video" else "jpg",
                                "size_bytes": None,
                                "download_url": item.get("url")
                            })

                if qualities:
                    return {
                        "success": True,
                        "title": data.get("filename") or "Downloaded Media",
                        "thumbnail": None,
                        "duration": 0,
                        "platform": "Cobalt Engine",
                        "qualities": qualities
                    }
        except Exception:
            continue

    return None


# --- ENGINE 2: LOCAL YT-DLP ENGINE ---
def extract_via_yt_dlp(target_url: str) -> Dict[str, Any]:
    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'allow_unplayable_formats': False,
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


# --- MASTER CASCADING CONTROLLER ---
def extract_media_cascading(target_url: str) -> Dict[str, Any]:
    # Phase 1: Try Local yt-dlp first
    try:
        return extract_via_yt_dlp(target_url)
    except Exception as local_err:
        print(f"Local extraction failed: {local_err}. Invoking Fallback Engine...")

    # Phase 2: If local extraction hits bot check or unsupported URL, execute Fallback Engine
    fallback_data = extract_via_cobalt_fallback(target_url)
    if fallback_data:
        return fallback_data

    raise Exception("Extraction failed across all primary and fallback processing nodes.")


# --- API ENDPOINTS ---

@app.get("/")
def health_check():
    return {"status": "online", "engine": "VidMax HD Cascading Engine 3.0"}


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
        data = await asyncio.to_thread(extract_media_cascading, url)
        data["cached"] = False
        set_to_cache(url, data)
        return data
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")
