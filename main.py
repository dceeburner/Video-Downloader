import asyncio
import time
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import requests
import yt_dlp

app = FastAPI(
    title="VidMax HD Extraction Engine",
    description="Multi-Platform Video Downloader Backend",
    version="3.5.0"
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


# --- CORE EXTRACTION LOGIC ---
def extract_media(target_url: str, base_host: str) -> Dict[str, Any]:
    # General robust extraction settings for yt-dlp
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'allow_unplayable_formats': False,
        'ignoreerrors': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target_url, download=False)
        if not info:
            raise Exception("Could not extract metadata from URL.")

        clean_info = ydl.sanitize_info(info)
        is_youtube = "youtube.com" in target_url or "youtu.be" in target_url

        qualities: List[Dict[str, Any]] = []
        seen_resolutions = set()

        raw_formats = clean_info.get("formats", [])

        # Reverse to prioritize highest quality
        for f in reversed(raw_formats):
            direct_url = f.get("url")
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            height = f.get("height")
            ext = f.get("ext", "mp4")

            if not direct_url:
                continue

            # Process Video + Audio or Combined Formats
            if height and height >= 144 and vcodec != "none":
                res_label = f"{height}p"
                if res_label not in seen_resolutions:
                    seen_resolutions.add(res_label)

                    # For YouTube, proxy through server to avoid 403 IP-lock
                    if is_youtube:
                        final_download_url = f"{base_host}/stream?stream_url={requests.utils.quote(direct_url)}"
                    else:
                        final_download_url = direct_url

                    qualities.append({
                        "quality": res_label,
                        "type": "video",
                        "extension": ext if ext != "m3u8" else "mp4",
                        "size_bytes": f.get("filesize") or f.get("filesize_approx"),
                        "download_url": final_download_url
                    })

            # Process Audio Only
            elif vcodec == "none" and acodec != "none" and "audio" not in seen_resolutions:
                seen_resolutions.add("audio")
                if is_youtube:
                    final_download_url = f"{base_host}/stream?stream_url={requests.utils.quote(direct_url)}"
                else:
                    final_download_url = direct_url

                qualities.append({
                    "quality": "Audio Only",
                    "type": "audio",
                    "extension": ext,
                    "size_bytes": f.get("filesize") or f.get("filesize_approx"),
                    "download_url": final_download_url
                })

        # Fallback if specific formats were not parsed separately
        if not qualities and clean_info.get("url"):
            direct_url = clean_info.get("url")
            if is_youtube:
                final_download_url = f"{base_host}/stream?stream_url={requests.utils.quote(direct_url)}"
            else:
                final_download_url = direct_url

            qualities.append({
                "quality": "HD Best Quality",
                "type": "video",
                "extension": clean_info.get("ext", "mp4"),
                "size_bytes": clean_info.get("filesize"),
                "download_url": final_download_url
            })

        return {
            "success": True,
            "title": clean_info.get("title", "VidMax HD Media"),
            "thumbnail": clean_info.get("thumbnail"),
            "duration": clean_info.get("duration", 0),
            "platform": clean_info.get("extractor_key", "Unknown"),
            "qualities": qualities
        }


# --- ENDPOINTS ---

@app.get("/")
def health_check():
    return {"status": "online", "engine": "VidMax HD Engine 3.5"}


@app.get("/download")
@app.get("/extract")
async def extract_video(url: str = Query(..., description="Target media URL")):
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required.")

    # Base domain of your Render deployment
    base_host = "https://video-downloader-o8c7.onrender.com"

    cached_data = get_from_cache(url)
    if cached_data:
        cached_data["cached"] = True
        return cached_data

    try:
        data = await asyncio.to_thread(extract_media, url, base_host)
        data["cached"] = False
        set_to_cache(url, data)
        return data
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Extraction error: {str(e)}")


@app.get("/stream")
async def proxy_stream(stream_url: str = Query(..., description="Encrypted/Encoded YouTube Stream URL")):
    """Proxies video streams through Render to fix 403 IP-binding errors on Android devices."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    }

    def iter_file():
        with requests.get(stream_url, headers=headers, stream=True) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

    try:
        return StreamingResponse(iter_file(), media_type="video/mp4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")
