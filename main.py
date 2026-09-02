from fastapi import FastAPI, HTTPException
import yt_dlp
import requests

# Create the FastAPI app
app = FastAPI(title="Social Media Video Downloader")

def clean_url(url: str) -> str:
    # Resolve Facebook share and shortened redirect links
    if "facebook.com/share" in url or "fb.watch" in url:
        try:
            response = requests.head(
                url,
                allow_redirects=True,
                timeout=5,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            return response.url.split('?')[0] # Strips tracking params like ?_fb_noscript=1
        except Exception:
            pass
    return url

@app.get("/download")
def get_video_info(url: str):
    """
    Extracts direct video download links and metadata without 
    downloading the actual video file to your server.
    """
    url = clean_url(url)

    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract metadata only (download=False)
            info = ydl.extract_info(url, download=False)
            
            return {
                "success": True,
                "title": info.get("title", "Unknown Title"),
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration"),
                "platform": info.get("extractor_key"),
                "video_url": info.get("url") # Direct .mp4 link
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))