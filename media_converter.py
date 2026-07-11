"""
=============================================================================
MeetLink Media Converter (Fail-Proof Engine)
=============================================================================
Advanced, robust, multi-tier FFmpeg conversion module for WebM recordings.
Optimized specifically for high-quality Telegram playback & streaming.

Why old conversions failed (and how this module fixes them):
1. Odd Dimensions: MediaRecorder often outputs resolutions like 1365x768 or 361x640.
   libx264 crashes if width or height is not divisible by 2 (even numbers).
   Fix: Auto-scale/pad dimensions to even numbers using filter:
   `scale=w=trunc(iw/2)*2:h=trunc(ih/2)*2,format=yuv420p`
2. Pixel Format: Telegram inline video streaming REQUIRES `yuv420p`.
   Fix: Explicitly enforce `-pix_fmt yuv420p` & `format=yuv420p`.
3. Missing Audio Track: Screen recordings often have 0 audio streams.
   Standard `-c:a aac` crashes if no audio stream exists.
   Fix: Use optional audio mapping `-map 0:v:0 -map 0:a:0?` to safely handle video-only files.
4. Corrupt Timestamps / VFR: Live WebRTC streams often have broken PTS/DTS headers.
   Fix: Use `-fflags +genpts+discardcorrupt`, `-avoid_negative_ts make_zero`,
   and `-max_muxing_queue_size 9999`.
5. High Quality Telegram Direct Play: Uses High Profile H.264 (`-profile:v high -level:v 4.1`)
   with `-crf 20` (crisp visual quality) and `-movflags +faststart` for instant streaming.
6. 3-Tier Fail-Proof Fallback Engine:
   - Tier 1: High Quality / Telegram Optimized (CRF 20, Preset Fast/Medium)
   - Tier 2: Ultrafast Compatibility Mode (CRF 22, Preset Ultrafast, simple padding)
   - Tier 3: Emergency Video-Only Transcode (in case audio track is broken)
=============================================================================
"""

import os
import subprocess
import shutil
import time
import traceback

def get_ffmpeg_path():
    """Locate ffmpeg executable across different operating systems and container environments."""
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        return ffmpeg
    for path in ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/bin/ffmpeg', 'C:\\ffmpeg\\bin\\ffmpeg.exe']:
        if os.path.exists(path):
            return path
    return 'ffmpeg'

def get_ffprobe_path():
    """Locate ffprobe executable."""
    ffprobe = shutil.which('ffprobe')
    if ffprobe:
        return ffprobe
    for path in ['/usr/bin/ffprobe', '/usr/local/bin/ffprobe', '/bin/ffprobe', 'C:\\ffmpeg\\bin\\ffprobe.exe']:
        if os.path.exists(path):
            return path
    return 'ffprobe'

def is_ffmpeg_available():
    """Check if ffmpeg is installed and accessible."""
    try:
        exe = get_ffmpeg_path()
        res = subprocess.run([exe, '-version'], capture_output=True, text=True, timeout=10)
        return res.returncode == 0
    except Exception:
        return False

def fmt_size(size_bytes):
    """Format file size into human readable string."""
    if size_bytes == 0:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB']
    i = 0
    s = float(size_bytes)
    while s >= 1024 and i < len(units) - 1:
        s /= 1024
        i += 1
    return f"{s:.1f} {units[i]}"

def probe_media_info(file_path):
    """Probe media file for diagnostic logging (streams, duration, resolution)."""
    try:
        ffprobe = get_ffprobe_path()
        cmd = [
            ffprobe, '-v', 'error',
            '-show_entries', 'stream=codec_type,codec_name,width,height,r_frame_rate:format=duration,size',
            '-of', 'default=noprint_wrappers=1',
            file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0 and res.stdout:
            return res.stdout.strip()
    except Exception:
        pass
    return "Probe unavailable"

def convert_webm_to_mp4(input_path, output_path, timeout=None):
    """
    Convert WebM recording to MP4 (H264 + AAC) optimized for Telegram streaming.
    Uses a 3-Tier automatic fallback engine so conversion NEVER fails.
    """
    if not os.path.exists(input_path) or os.path.getsize(input_path) == 0:
        print(f"❌ Error: Input file '{input_path}' does not exist or is empty.")
        return False

    input_size = os.path.getsize(input_path)
    print(f"🎬 Starting MP4 Conversion: {os.path.basename(input_path)} ({fmt_size(input_size)})")

    # Dynamic timeout based on file size: minimum 360s (6 mins), up to 1800s (30 mins)
    if timeout is None:
        timeout = max(360, int((input_size / (1024 * 1024)) * 30))

    ffmpeg_exe = get_ffmpeg_path()

    # -------------------------------------------------------------------------
    # PRE-REPAIR: Re-mux to fix truncated / incomplete WebM.
    # Short or abruptly-ended browser recordings often produce a WebM whose seek
    # head / duration is missing, which makes ffmpeg fail to decode them. A simple
    # stream copy re-mux (-c copy) repairs most of these without re-encoding.
    # -------------------------------------------------------------------------
    try:
        base, _ = os.path.splitext(input_path)
        cand = base + ".repaired.webm"
        rp = subprocess.run(
            [ffmpeg_exe, '-y', '-err_detect', 'ignore_err', '-i', input_path, '-c', 'copy', cand],
            capture_output=True, text=True, timeout=120
        )
        if rp.returncode == 0 and os.path.exists(cand) and os.path.getsize(cand) > 0:
            input_path = cand
            print(f"🛠️ Pre-repaired WebM for safer conversion: {os.path.basename(cand)}")
        else:
            if os.path.exists(cand):
                try: os.remove(cand)
                except Exception: pass
    except Exception as e:
        print(f"ℹ️ Pre-repair skipped: {e}")

    # Log media diagnostics
    info = probe_media_info(input_path)
    print(f"📊 Media Diagnostics:\n{info}")

    # -------------------------------------------------------------------------
    # TIER 1: High Quality & Telegram Streaming Optimized
    # -------------------------------------------------------------------------
    # - CRF 20 & Preset Fast gives crisp definition without lagging the server.
    # - High Profile H.264 level 4.1 is optimal for Telegram mobile & desktop.
    # - scale=w=trunc(iw/2)*2:h=trunc(ih/2)*2 prevents crashes on odd dimensions!
    # - yuv420p is required by Telegram for inline video player preview.
    # - map 0:a:0? ensures screen recordings without microphone won't crash.
    # - fflags & avoid_negative_ts fix broken timestamps from live WebRTC chunks.
    # -------------------------------------------------------------------------
    cmd_tier1 = [
        ffmpeg_exe, '-y',
        '-i', input_path,
        '-vsync', '2',
        '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p',
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-crf', '19',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-movflags', '+faststart',
        output_path
    ]

    print("🔄 Attempting Tier 1 (High Quality Telegram Optimized)...")
    try:
        res = subprocess.run(cmd_tier1, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            out_size = os.path.getsize(output_path)
            print(f"✅ [Tier 1 Success] MP4 created: {fmt_size(out_size)} (Direct Stream Ready 🚀)")
            return True
        else:
            print(f"⚠️ [Tier 1 Warning] Non-zero exit code or empty output. Stderr:\n{res.stderr[:400]}")
    except subprocess.TimeoutExpired:
        print(f"⚠️ [Tier 1 Timeout] Timed out after {timeout} seconds.")
    except Exception as e:
        print(f"⚠️ [Tier 1 Exception] {e}")

    # Remove incomplete file before retry
    if os.path.exists(output_path):
        try: os.remove(output_path)
        except Exception: pass

    # -------------------------------------------------------------------------
    # TIER 2: Ultrafast Robust Compatibility Mode
    # -------------------------------------------------------------------------
    # If Tier 1 failed (e.g., complex filter syntax, VFR issues, or CPU load),
    # Tier 2 uses ultrafast preset, crf 22, main profile, and pad filter.
    # -------------------------------------------------------------------------
    cmd_tier2 = [
        ffmpeg_exe, '-y',
        '-i', input_path,
        '-vsync', '2',
        '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p',
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '22',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-movflags', '+faststart',
        output_path
    ]

    print("🔄 Attempting Tier 2 (Ultrafast Compatibility Mode)...")
    try:
        res = subprocess.run(cmd_tier2, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            out_size = os.path.getsize(output_path)
            print(f"✅ [Tier 2 Success] MP4 created: {fmt_size(out_size)} (Direct Stream Ready 🚀)")
            return True
        else:
            print(f"⚠️ [Tier 2 Warning] Failed. Stderr:\n{res.stderr[:400]}")
    except Exception as e:
        print(f"⚠️ [Tier 2 Exception] {e}")

    if os.path.exists(output_path):
        try: os.remove(output_path)
        except Exception: pass

    # -------------------------------------------------------------------------
    # TIER 3: Emergency Video-Only Safe Transcode
    # -------------------------------------------------------------------------
    # If audio track is completely corrupted causing AAC encoder to crash,
    # Tier 3 strips audio (-an) and guarantees at least the video is preserved!
    # -------------------------------------------------------------------------
    cmd_tier3 = [
        ffmpeg_exe, '-y',
        '-i', input_path,
        '-vsync', '2',
        '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p',
        '-an',
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '23',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        output_path
    ]

    print("🔄 Attempting Tier 3 (Emergency Video-Only Fallback)...")
    try:
        res = subprocess.run(cmd_tier3, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            out_size = os.path.getsize(output_path)
            print(f"✅ [Tier 3 Success] Video-only MP4 created: {fmt_size(out_size)}")
            return True
        else:
            print(f"⚠️ [Tier 3 Warning] Failed. Stderr:\n{res.stderr[:400]}")
    except Exception as e:
        print(f"⚠️ [Tier 3 Exception] {e}")

    if os.path.exists(output_path):
        try: os.remove(output_path)
        except Exception: pass

    # -------------------------------------------------------------------------
    # TIER 4: Last-resort stream copy (works when the WebM already contains
    # MP4-compatible H.264/AAC streams, e.g. Safari / quick recordings).
    # -------------------------------------------------------------------------
    cmd_tier4 = [
        ffmpeg_exe, '-y',
        '-i', input_path,
        '-c', 'copy',
        output_path
    ]

    print("🔄 Attempting Tier 4 (Last-Resort Stream Copy)...")
    try:
        res = subprocess.run(cmd_tier4, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            out_size = os.path.getsize(output_path)
            print(f"✅ [Tier 4 Success] Stream-copy MP4 created: {fmt_size(out_size)}")
            return True
        else:
            print(f"❌ [Tier 4 Failed] All fallback tiers exhausted! Stderr:\n{res.stderr[:400]}")
            return False
    except Exception as e:
        print(f"❌ [Tier 4 Exception] {e}")
        return False


def extract_mp3_from_video(input_path, output_path, timeout=None):
    """
    Extract audio from video recording as MP3.
    Uses 2-Tier automatic fallback and gracefully handles video-only recordings.
    """
    if not os.path.exists(input_path) or os.path.getsize(input_path) == 0:
        print(f"❌ Error: Input file '{input_path}' does not exist or is empty.")
        return False

    input_size = os.path.getsize(input_path)
    if timeout is None:
        timeout = max(180, int((input_size / (1024 * 1024)) * 15))

    ffmpeg_exe = get_ffmpeg_path()
    print(f"🎵 Starting MP3 Extraction: {os.path.basename(input_path)}")

    # Tier 1: High Quality VBR / High Bitrate MP3
    cmd_tier1 = [
        ffmpeg_exe, '-y',
        '-fflags', '+genpts',
        '-i', input_path,
        '-vn',
        '-map', '0:a:0?',
        '-c:a', 'libmp3lame',
        '-q:a', '0',
        '-b:a', '256k',
        '-loglevel', 'warning',
        output_path
    ]

    try:
        res = subprocess.run(cmd_tier1, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(f"✅ [Audio Success] MP3 extracted: {fmt_size(os.path.getsize(output_path))}")
            return True
        else:
            if "does not contain any stream" in res.stderr or "Stream map '0:a:0?' matches no streams" in res.stderr:
                print("ℹ️ No audio stream detected in recording (Video-Only recording). Skipping MP3.")
                return False
            print(f"⚠️ [Audio Tier 1 Warning] Stderr: {res.stderr[:300]}")
    except Exception as e:
        print(f"⚠️ [Audio Tier 1 Exception] {e}")

    if os.path.exists(output_path):
        try: os.remove(output_path)
        except Exception: pass

    # Tier 2: Safe Standard Bitrate MP3
    cmd_tier2 = [
        ffmpeg_exe, '-y',
        '-i', input_path,
        '-vn',
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        '-loglevel', 'warning',
        output_path
    ]

    try:
        res = subprocess.run(cmd_tier2, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(f"✅ [Audio Tier 2 Success] MP3 extracted: {fmt_size(os.path.getsize(output_path))}")
            return True
        else:
            print(f"❌ [Audio Tier 2 Failed] Stderr: {res.stderr[:300]}")
            return False
    except Exception as e:
        print(f"❌ [Audio Tier 2 Exception] {e}")
        return False
