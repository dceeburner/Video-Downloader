import os
import random
import requests
import yt_dlp
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List

app = FastAPI(title="VidMax HD Backend")

# Enable CORS for Android client requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. Load proxies from Render environment variables
RAW_PROXIES = os.getenv("PROXY_LIST", "")
PROXY_POOL = [p.strip() for p in RAW_PROXIES.split(",") if p.strip()]

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

@app.get("/")
def home():
    return {
        "status": "online",
        "proxies_loaded": len(PROXY_POOL),
        "engine": "VidMax HD Supersonic 3.6"
    }

@app.get("/download")
@app.get("/extract")
async def extract_media(url: str = Query(...)):
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")

    target_url = clean_input_url(url)
    selected_proxy = get_random_proxy()

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        },
    }

    # Pass selected proxy into yt-dlp if available
    if selected_proxy:
        ydl_opts['proxy'] = selected_proxy
        ydl_opts['geo_verification_proxy'] = selected_proxy

    try:
        # Run yt-dlp in a thread to avoid blocking the event loop
        import asyncio
        info = await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(target_url, download=False))

        qualities = []
        formats = info.get('formats', [])

        # Sort formats by quality (height) in descending order
        sorted_formats = sorted(formats, key=lambda x: (x.get('height') or 0), reverse=True)

        seen_res = set()
        for fmt in sorted_formats:
            format_url = fmt.get('url')
            if format_url:
                res = fmt.get('format_note') or fmt.get('resolution') or 'SD'
                # Avoid duplicates in the quality list
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

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")

@app.get("/stream")
async def proxy_stream(stream_url: str = Query(...)):
    """Proxies direct video chunks through Webshare to bypass 403 blocks on client devices."""
    selected_proxy = get_random_proxy()
    proxies_dict = {"http": selected_proxy, "https": selected_proxy} if selected_proxy else None

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }

    def iter_file():
        # Use requests with the selected proxy
        with requests.get(stream_url, headers=headers, proxies=proxies_dict, stream=True) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

    try:
        return StreamingResponse(iter_file(), media_type="video/mp4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stream proxy error: {str(e)}")
