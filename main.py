from fastapi import FastAPI, HTTPException
import yt_dlp
import requests

# Create the FastAPI app
app = FastAPI(title="Social Media Video Downloader")

@app.get("/")
def health_check():
    return {"status": "online"}

def clean_url(url: str) -> str:
    # Resolve Facebook share and shortened redirect links
    if "facebook.com/share" in url or "fb.watch" in url:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            # Use GET with stream=True to follow full redirects without downloading the video body
            response = requests.get(url, allow_redirects=True, timeout=8, headers=headers, stream=True)
            final_url = response.url

            # Clean off noscript tracking flags without breaking 'watch?v=' parameters
            final_url = final_url.replace("?_fb_noscript=1", "").replace("&_fb_noscript=1", "")
            return final_url
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