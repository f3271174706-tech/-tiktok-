import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

DOWNLOADS_DIR = Path(__file__).parent / "downloads"

# Simple in-memory cache to avoid hitting Douyin repeatedly for the same URL
_cache: dict = {}
_CACHE_TTL = 600  # 10 minutes


def _cache_get(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry and time.time() - entry["_ts"] < _CACHE_TTL:
        return entry
    return None


def _cache_set(key: str, info: dict):
    info["_ts"] = time.time()
    _cache[key] = info

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)


def _ensure_downloads_dir():
    DOWNLOADS_DIR.mkdir(exist_ok=True)


def _extract_url(text: str) -> str:
    """Extract and normalize the first URL from user input (may contain share text)."""
    text = text.strip()
    url_match = re.search(
        r"(https?://\S+|v\.douyin\.com/\S+|vm\.tiktok\.com/\S+|vt\.tiktok\.com/\S+)",
        text,
    )
    if url_match:
        url = url_match.group(1)
        url = url.rstrip(".,;:!?，。；：！？)")
    else:
        url = text

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url


def _is_douyin(url: str) -> bool:
    return any(d in url for d in ["douyin.com", "iesdouyin.com"])


def _resolve_douyin(url: str) -> tuple[str, str]:
    """Resolve a Douyin short link. Returns (id, type) where type is 'video' or 'note'."""
    # Direct URL patterns — slides also use note endpoint for data
    for pattern, content_type in [(r"/video/(\d+)", "video"), (r"/note/(\d+)", "note"), (r"/slides/(\d+)", "note")]:
        match = re.search(pattern, url)
        if match:
            return match.group(1), content_type

    # Resolve short link (302 redirect)
    headers = {"User-Agent": MOBILE_UA}
    r = httpx.get(url, headers=headers, follow_redirects=False, timeout=30)

    if r.status_code in (301, 302):
        location = r.headers.get("location", "")
        for pattern, content_type in [(r"/video/(\d+)", "video"), (r"/note/(\d+)", "note"), (r"/slides/(\d+)", "note")]:
            match = re.search(pattern, location)
            if match:
                return match.group(1), content_type

    # Follow full redirect chain
    r = httpx.get(url, headers=headers, follow_redirects=True, timeout=30)
    final_url = str(r.url)
    for pattern, content_type in [(r"/video/(\d+)", "video"), (r"/note/(\d+)", "note"), (r"/slides/(\d+)", "note")]:
        match = re.search(pattern, final_url)
        if match:
            return match.group(1), content_type

    raise ValueError(f"无法从链接中解析: {url}")


def _extract_douyin(url: str) -> dict:
    """Extract Douyin video or photo note info by scraping the mobile share page."""
    item_id, content_type = _resolve_douyin(url)
    path_segment = "note" if content_type == "note" else "video"
    share_url = f"https://www.iesdouyin.com/share/{path_segment}/{item_id}/"
    headers = {"User-Agent": MOBILE_UA, "Referer": share_url}

    r = httpx.get(share_url, headers=headers, follow_redirects=True, timeout=30)
    html = r.text

    # Extract title
    title = "未知标题"
    desc_match = re.search(r'"desc":"([^"]{1,300})"', html)
    if desc_match:
        title = json.loads('"' + desc_match.group(1) + '"')

    # Extract thumbnail (cover image, prefer high-res)
    thumbnail = ""
    cover_urls = re.findall(r"https:[^\"\s]*douyinpic\.com[^\"\s]*", html)
    for raw in cover_urls:
        try:
            decoded = json.loads('"' + raw + '"')
        except json.JSONDecodeError:
            decoded = raw
        if "avatar" in decoded or "100x100" in decoded:
            continue
        if not thumbnail or "1080x1080" in decoded:
            thumbnail = decoded
            if "1080x1080" in decoded:
                break

    # Extract background music MP3 (for slides)
    music_url = ""
    music_match = re.search(r'"play_addr":\{"uri":"([^"]+\.mp3)"', html)
    if music_match:
        music_url = json.loads('"' + music_match.group(1) + '"')

    if content_type == "note":
        # Extract all images from photo note (use bracket counting for nested JSON)
        images = []
        img_start = html.find('"images":[')
        if img_start >= 0:
            arr_start = img_start + 9  # position of opening '[' after "images":
            depth = 0
            end = arr_start
            for i in range(arr_start, min(arr_start + 50000, len(html))):
                if html[i] == "[":
                    depth += 1
                elif html[i] == "]":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            img_data = html[arr_start:end]
            # Each image: "url_list":["URL1","URL2",...] — grab the first URL (best quality)
            for block in re.finditer(r'"url_list":\[\"(https:[^\"]+)"', img_data):
                raw = block.group(1)
                try:
                    img_url = json.loads('"' + raw + '"')
                except json.JSONDecodeError:
                    img_url = raw
                if img_url not in images:
                    images.append(img_url)

        return {
            "title": title,
            "thumbnail": images[0] if images else thumbnail,
            "duration": 0,
            "type": "photo",
            "images": images,
            "music_url": music_url,
            "platform": "douyin",
        }

    # Video post
    duration = 0
    dur_match = re.search(r'"duration":(\d+)', html)
    if dur_match:
        duration = int(dur_match.group(1))

    video_url = ""
    play_match = re.search(r'"url_list":\["(https:[^"]+playwm[^"]+)"\]', html)
    if play_match:
        wm_url = json.loads('"' + play_match.group(1) + '"')
        video_url = wm_url.replace("/playwm/", "/play/")

    return {
        "title": title,
        "thumbnail": thumbnail,
        "duration": duration,
        "type": "video",
        "video_url": video_url,
        "platform": "douyin",
    }


def _extract_tiktok(url: str) -> dict:
    """Extract TikTok video info using yt-dlp with browser impersonation."""
    target = ImpersonateTarget.from_str("chrome")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "impersonate": target,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats", [])
    video_url = ""
    best_height = 0

    for fmt in formats:
        height = fmt.get("height") or 0
        if fmt.get("vcodec") != "none" and fmt.get("acodec") == "none" and height > best_height:
            video_url = fmt.get("url")
            best_height = height

    if not video_url:
        for fmt in formats:
            if fmt.get("vcodec") != "none":
                video_url = fmt.get("url")
                break
    if not video_url:
        video_url = info.get("url", "")

    return {
        "title": info.get("title", "未知标题"),
        "thumbnail": info.get("thumbnail", ""),
        "duration": info.get("duration", 0),
        "type": "video",
        "video_url": video_url,
        "platform": "tiktok",
    }


def apply_quality(video_url: str, quality: str) -> str:
    """Apply quality setting to a Douyin video URL."""
    ratio_map = {"720p": "720p", "1080p": "1080p", "hd": "1080p"}
    ratio = ratio_map.get(quality, "1080p")
    if "ratio=" in video_url:
        return re.sub(r"ratio=\w+", f"ratio={ratio}", video_url)
    return video_url + f"&ratio={ratio}"


def extract_video_info(url: str) -> dict:
    """Extract media metadata. Uses direct HTTP for Douyin, yt-dlp for TikTok."""
    url = _extract_url(url)
    cached = _cache_get(url)
    if cached:
        return {k: v for k, v in cached.items() if not k.startswith("_")}

    if _is_douyin(url):
        info = _extract_douyin(url)
    else:
        info = _extract_tiktok(url)
    _cache_set(url, info)
    return {k: v for k, v in info.items() if not k.startswith("_")}


def _convert_to_mp3(video_path: str) -> str:
    """Convert video to MP3 audio using ffmpeg, returns the mp3 file path."""
    mp3_path = str(Path(video_path).with_suffix(".mp3"))
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", mp3_path],
        capture_output=True,
        check=True,
    )
    os.remove(video_path)
    return mp3_path


def _make_slides_video(info: dict) -> str:
    """Combine slides images + music into an MP4 video using ffmpeg."""
    images = info.get("images", [])
    music_url = info.get("music_url", "")
    if not images:
        raise ValueError("No images to convert")

    headers = {"User-Agent": MOBILE_UA, "Referer": "https://www.iesdouyin.com/"}
    img_count = len(images)

    # Download music and get duration
    music_path = None
    image_duration = 3.0
    if music_url:
        r = httpx.get(music_url, headers=headers, follow_redirects=True, timeout=60)
        music_path = str(DOWNLOADS_DIR / f"_slide_a_{uuid.uuid4().hex[:8]}.mp3")
        with open(music_path, "wb") as f:
            f.write(r.content)
        image_duration = 3.0

    # Step 1: Convert each image to a short video clip
    vid_paths = []
    for i, img_url in enumerate(images):
        r = httpx.get(img_url, headers=headers, follow_redirects=True, timeout=60)
        img_tmp = str(DOWNLOADS_DIR / f"_slide_img_{uuid.uuid4().hex[:4]}.webp")
        with open(img_tmp, "wb") as f:
            f.write(r.content)
        vid_path = str(DOWNLOADS_DIR / f"_slide_v_{i}_{uuid.uuid4().hex[:4]}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-loop", "1", "-i", img_tmp,
             "-c:v", "libx264", "-t", f"{image_duration:.2f}",
             "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "23",
             "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
             vid_path],
            capture_output=True, check=True,
        )
        os.remove(img_tmp)
        vid_paths.append(vid_path)

    # Step 2: Concat all clips + add audio
    concat_file = str(DOWNLOADS_DIR / f"_concat_{uuid.uuid4().hex[:4]}.txt")
    with open(concat_file, "w") as f:
        for v in vid_paths:
            f.write(f"file '{v}'\n")

    safe_name = re.sub(r'[\n\r\t\\/*?:"<>|#]', '', info["title"])[:50] or uuid.uuid4().hex[:12]
    out_path = str(DOWNLOADS_DIR / f"{safe_name}.mp4")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file]
    if music_path:
        cmd += ["-i", music_path, "-c:v", "copy", "-c:a", "aac", "-shortest", "-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-c:v", "copy"]
    cmd.append(out_path)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[:500]}")

    # Cleanup temp files
    for p in vid_paths:
        try: os.remove(p)
        except OSError: pass
    if music_path:
        try: os.remove(music_path)
        except OSError: pass
    try: os.remove(concat_file)
    except OSError: pass

    _schedule_cleanup(out_path)
    return out_path


def download_video(
    url: str, quality: str = "1080p", media_type: str = "video", image_index: int = 0
) -> tuple[str, str]:
    """Download video/image or extract audio. Returns (file_path, filename)."""
    url = _extract_url(url)
    _ensure_downloads_dir()

    if _is_douyin(url):
        info = extract_video_info(url)  # uses cache
        if info["type"] == "photo":
            if media_type == "video" and info.get("music_url"):
                # Slides with music → make slideshow video
                out_path = _make_slides_video(info)
                filename = os.path.basename(out_path)
                return out_path, filename
            elif media_type == "mp3" and info.get("music_url"):
                # Download music directly
                r = httpx.get(info["music_url"], headers={"User-Agent": MOBILE_UA}, follow_redirects=True, timeout=60)
                safe_title = re.sub(r'[\n\r\t\\/*?:"<>|#]', '', info["title"])[:50]
                filename = f"{safe_title}.mp3" if safe_title else f"{uuid.uuid4().hex[:12]}.mp3"
                filepath = str(DOWNLOADS_DIR / filename)
                with open(filepath, "wb") as f:
                    f.write(r.content)
                _schedule_cleanup(filepath)
                return filepath, filename
            else:
                # image type → download single photo
                filepath, filename = _download_single_photo(info, image_index)
        else:
            filepath, filename = _download_douyin_video(info, quality)
    else:
        filepath, filename = _download_tiktok(url)

    if media_type == "mp3" and not filename.endswith(".mp3") and not filename.endswith(".zip"):
        mp3_path = _convert_to_mp3(filepath)
        mp3_name = str(Path(filename).with_suffix(".mp3"))
        _schedule_cleanup(mp3_path)
        return mp3_path, mp3_name

    return filepath, filename


def _download_douyin_video(info: dict, quality: str = "1080p") -> tuple[str, str]:
    """Download Douyin video via direct HTTP."""
    video_url = info["video_url"]
    if not video_url:
        raise ValueError("未能提取视频下载地址")

    video_url = apply_quality(video_url, quality)

    headers = {"User-Agent": MOBILE_UA, "Referer": "https://www.iesdouyin.com/"}
    r = httpx.get(video_url, headers=headers, follow_redirects=True, timeout=120)

    safe_title = re.sub(r'[\n\r\t\\/*?:"<>|#]', '', info["title"])[:50]
    filename = f"{safe_title}.mp4" if safe_title else f"{uuid.uuid4().hex[:12]}.mp4"
    filepath = str(DOWNLOADS_DIR / filename)

    with open(filepath, "wb") as f:
        f.write(r.content)

    _schedule_cleanup(filepath)
    return filepath, filename


def _download_single_photo(info: dict, index: int = 0) -> tuple[str, str]:
    """Download a single image from a Douyin photo note."""
    images = info.get("images", [])
    if not images:
        raise ValueError("未能提取图片地址")

    idx = max(0, min(index, len(images) - 1))
    img_url = images[idx]

    headers = {"User-Agent": MOBILE_UA, "Referer": "https://www.iesdouyin.com/"}
    r = httpx.get(img_url, headers=headers, follow_redirects=True, timeout=60)

    # Determine extension
    ct = r.headers.get("content-type", "")
    if "jpeg" in ct or "jpg" in ct:
        ext = ".jpg"
    elif "png" in ct:
        ext = ".png"
    elif "gif" in ct:
        ext = ".gif"
    elif "webp" in ct:
        ext = ".webp"
    else:
        # Fallback: guess from URL
        if ".gif" in img_url:
            ext = ".gif"
        elif ".png" in img_url:
            ext = ".png"
        elif ".jpg" in img_url or ".jpeg" in img_url:
            ext = ".jpg"
        else:
            ext = ".webp"

    safe_title = re.sub(r'[\n\r\t\\/*?:"<>|#]', '', info["title"])[:50]
    filename = f"{safe_title}_{idx+1}{ext}" if safe_title else f"{uuid.uuid4().hex[:12]}{ext}"
    filepath = str(DOWNLOADS_DIR / filename)

    with open(filepath, "wb") as f:
        f.write(r.content)

    _schedule_cleanup(filepath)
    return filepath, filename


def _download_tiktok(url: str) -> tuple[str, str]:
    """Download TikTok video via yt-dlp."""
    video_id = uuid.uuid4().hex[:12]
    output_template = str(DOWNLOADS_DIR / f"{video_id}.%(ext)s")

    target = ImpersonateTarget.from_str("chrome")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": output_template,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "merge_output_format": "mp4",
        "impersonate": target,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

    actual_path = filename
    base = Path(filename).stem
    for f in DOWNLOADS_DIR.iterdir():
        if f.stem == base:
            actual_path = str(f)
            break

    _schedule_cleanup(actual_path)
    return actual_path, os.path.basename(actual_path)


def download_video_for_stream(video_url: str) -> tuple[str, str]:
    """Download a video from its CDN URL for streaming. Returns (filepath, filename)."""
    _ensure_downloads_dir()

    headers = {"User-Agent": MOBILE_UA, "Referer": "https://www.iesdouyin.com/"}
    r = httpx.get(video_url, headers=headers, follow_redirects=True, timeout=120)

    safe_name = f"{uuid.uuid4().hex[:12]}.mp4"
    filepath = str(DOWNLOADS_DIR / safe_name)

    with open(filepath, "wb") as f:
        f.write(r.content)

    _schedule_cleanup(filepath)
    return filepath, safe_name


def _schedule_cleanup(filepath: str):
    """Delete file after 10 minutes."""
    def _cleanup():
        import time
        time.sleep(600)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except OSError:
            pass

    t = threading.Thread(target=_cleanup, daemon=True)
    t.start()


def cleanup_old_files(max_age_seconds: int = 1800):
    """Delete download files older than max_age_seconds (default 30 min). Called at startup."""
    _ensure_downloads_dir()
    now = time.time()
    deleted = 0
    for f in DOWNLOADS_DIR.iterdir():
        if f.is_file():
            try:
                if now - f.stat().st_mtime > max_age_seconds:
                    f.unlink()
                    deleted += 1
            except OSError:
                pass
    if deleted:
        print(f"[cleanup] Removed {deleted} old file(s) from downloads/")
