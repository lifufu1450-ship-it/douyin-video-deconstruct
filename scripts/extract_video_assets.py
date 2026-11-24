#!/usr/bin/env python3
"""
抖音视频本地拆片预处理脚本（跨平台可移植版）：
1. 可直接处理本地视频，或传入 --video-url / --audio-url 自动下载并用 ffmpeg 无损封装
2. 自动解析系统 ffmpeg 或 imageio-ffmpeg 内置二进制（不硬编码用户目录）
3. 抽取 16kHz 单声道音频，优先使用 mlx-whisper（Apple Silicon），自动回退 faster-whisper（Intel/Win/Linux）
4. 生成带时间戳的逐字稿、场景切镜节奏数据、4x3 分镜故事板（storyboard_*.jpg）
5. 支持 --sub-times 模式批量截取原片画面字幕带拼图，用于方言/谐音二次精校
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DEFAULT_MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_FASTER_MODEL = "small"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def parse_douyin_share_page(share_text: str) -> dict:
    """
    通过抖音移动端公开分享页（iesdouyin.com/share/video/<id>）解析公开元数据与 MP4 播放地址。
    不绕过任何验证码或权限校验；若返回非公开/风控页面直接抛出明确错误。
    """
    urls = re.findall(r"https?://[^\s\"'<>]+", share_text)
    if not urls:
        raise RuntimeError("未在输入中找到有效的抖音分享链接。")
    raw_url = urls[0]

    # 1. 解析短链重定向得到 video_id
    req1 = urllib.request.Request(raw_url, headers={"User-Agent": MOBILE_UA})
    with urllib.request.urlopen(req1, timeout=20) as resp1:
        final_url = resp1.geturl()

    m_id = re.search(r"/(?:video|note|share/video)/(\d+)", final_url)
    if not m_id:
        parts = [p for p in final_url.split("?")[0].strip("/").split("/") if p.isdigit()]
        if not parts:
            raise RuntimeError(f"无法从链接解析视频 ID: {final_url}")
        video_id = parts[-1]
    else:
        video_id = m_id.group(1)

    share_url = f"https://www.iesdouyin.com/share/video/{video_id}"
    req2 = urllib.request.Request(share_url, headers={"User-Agent": MOBILE_UA})
    with urllib.request.urlopen(req2, timeout=20) as resp2:
        html_text = resp2.read().decode("utf-8", errors="ignore")

    if any(k in html_text for k in ("captcha", "verifycenter", "请完成验证")):
        raise RuntimeError("移动端分享页触发人机验证，已按合规原则停止。")

    m_router = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})</script>", html_text, re.S)
    if not m_router:
        raise RuntimeError("未能从公开分享页读取到 window._ROUTER_DATA，可能页面结构已变更或内容非公开。")

    router_data = json.loads(m_router.group(1))
    loader_data = router_data.get("loaderData", {})
    item = None
    for v in loader_data.values():
        if isinstance(v, dict):
            res = v.get("videoInfoRes") or v.get("itemInfo")
            if isinstance(res, dict):
                lst = res.get("item_list") or []
                if lst:
                    item = lst[0]
                    break
    if not item:
        raise RuntimeError("公开分享页未返回视频数据（视频可能已删除、设为私密或受地区限制）。")

    play_addr = (item.get("video") or {}).get("play_addr") or {}
    url_list = play_addr.get("url_list") or []
    if not url_list:
        raise RuntimeError("公开分享页未包含可播放视频地址。")

    play_url = url_list[0].replace("playwm", "play")
    author = (item.get("author") or {}).get("nickname") or "未获取"
    desc = (item.get("desc") or "").strip()
    stats = item.get("statistics") or {}

    return {
        "video_id": video_id,
        "share_url": share_url,
        "canonical_url": f"https://www.douyin.com/video/{video_id}",
        "author": author,
        "desc": desc,
        "statistics": {
            "digg_count": stats.get("digg_count"),
            "comment_count": stats.get("comment_count"),
            "collect_count": stats.get("collect_count"),
            "share_count": stats.get("share_count"),
        },
        "play_url": play_url,
    }


def find_ffmpeg() -> str:
    candidates = [
        shutil.which("ffmpeg"),
        str(Path.home() / ".local" / "bin" / "ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    if importlib.util.find_spec("imageio_ffmpeg") is not None:
        import imageio_ffmpeg

        ff = imageio_ffmpeg.get_ffmpeg_exe()
        if ff and os.path.isfile(ff):
            return ff
    raise RuntimeError("未找到 ffmpeg，请先运行本 Skill 的 scripts/setup_env.py 自动安装依赖。")


def load_font(size: int = 16):
    font_candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for fp in font_candidates:
        if os.path.isfile(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def download_media(
    url: str, dest: Path, referer: str = "https://www.douyin.com/", user_agent: str = BROWSER_UA
) -> Path:
    req = urllib.request.Request(
        url,
        headers={
            "Referer": referer,
            "User-Agent": user_agent,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"媒体流下载为空: {dest.name}")
    return dest


def download_and_mux(ffmpeg_bin: str, video_url: str, audio_url: str | None, out_dir: Path) -> Path:
    raw_v = out_dir / "stream_video.m4v"
    download_media(video_url, raw_v)
    final_mp4 = out_dir / "source_video.mp4"
    if audio_url:
        raw_a = out_dir / "stream_audio.m4a"
        download_media(audio_url, raw_a)
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(raw_v),
                "-i",
                str(raw_a),
                "-c",
                "copy",
                str(final_mp4),
            ],
            check=True,
        )
    else:
        shutil.move(str(raw_v), str(final_mp4))
    return final_mp4


def probe_video(ffmpeg_bin: str, video_path: Path) -> dict:
    """用 ffmpeg -i 解析基础视频元数据（不依赖 ffprobe）。"""
    proc = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-i", str(video_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    info = proc.stderr
    meta = {
        "file": str(video_path.resolve()),
        "file_size_mb": round(video_path.stat().st_size / (1024 * 1024), 2),
        "duration_sec": None,
        "duration_str": None,
        "width": None,
        "height": None,
        "fps": None,
        "video_codec": None,
        "audio_codec": None,
    }

    m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if m_dur:
        h, m, s = int(m_dur.group(1)), int(m_dur.group(2)), float(m_dur.group(3))
        total = round(h * 3600 + m * 60 + s, 2)
        meta["duration_sec"] = total
        meta["duration_str"] = f"{int(total // 60):02d}:{total % 60:05.2f}"

    m_vid = re.search(r"Stream #\d+:\d+.*?Video:\s*([^,\s]+).*?,\s*(\d{2,5})x(\d{2,5})", info)
    if m_vid:
        meta["video_codec"] = m_vid.group(1)
        meta["width"] = int(m_vid.group(2))
        meta["height"] = int(m_vid.group(3))

    m_fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", info)
    if m_fps:
        meta["fps"] = float(m_fps.group(1))

    m_aud = re.search(r"Stream #\d+:\d+.*?Audio:\s*([^,\s]+)", info)
    if m_aud:
        meta["audio_codec"] = m_aud.group(1)

    return meta


def format_ts(sec: float) -> str:
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    s = sec % 60
    return f"{m:02d}:{s:05.2f}"


def extract_audio(ffmpeg_bin: str, video_path: Path, wav_path: Path) -> None:
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ],
        check=True,
    )


def transcribe_audio(wav_path: Path, model_name: str | None, out_dir: Path) -> dict:
    prompt = "以下是普通话抖音短视频口播字幕，请使用简体中文并加上标点符号。"
    segments = []
    lines = []
    used_backend = None
    used_model = None

    if importlib.util.find_spec("mlx_whisper") is not None:
        import mlx_whisper

        used_backend = "mlx_whisper"
        used_model = model_name or DEFAULT_MLX_MODEL
        result = mlx_whisper.transcribe(
            str(wav_path),
            path_or_hf_repo=used_model,
            language="zh",
            initial_prompt=prompt,
        )
        for seg in result.get("segments", []):
            st = round(float(seg.get("start", 0.0)), 2)
            ed = round(float(seg.get("end", 0.0)), 2)
            txt = (seg.get("text") or "").strip()
            if txt:
                segments.append({"start": st, "end": ed, "text": txt})
                lines.append(f"[{format_ts(st)} - {format_ts(ed)}] {txt}")
        full_text = (result.get("text") or "").strip()
    elif importlib.util.find_spec("faster_whisper") is not None:
        from faster_whisper import WhisperModel

        used_backend = "faster_whisper"
        used_model = model_name or DEFAULT_FASTER_MODEL
        model = WhisperModel(used_model, device="auto", compute_type="int8")
        seg_iter, _ = model.transcribe(str(wav_path), language="zh", initial_prompt=prompt)
        parts = []
        for seg in seg_iter:
            st = round(float(seg.start), 2)
            ed = round(float(seg.end), 2)
            txt = (seg.text or "").strip()
            if txt:
                segments.append({"start": st, "end": ed, "text": txt})
                lines.append(f"[{format_ts(st)} - {format_ts(ed)}] {txt}")
                parts.append(txt)
        full_text = "".join(parts)
    else:
        raise RuntimeError(
            "未安装 mlx_whisper 或 faster_whisper，请先运行本 Skill 的 scripts/setup_env.py 自动安装。"
        )

    transcript_json = {
        "backend": used_backend,
        "model": used_model,
        "text": full_text,
        "segment_count": len(segments),
        "segments": segments,
    }
    (out_dir / "transcript.json").write_text(
        json.dumps(transcript_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "transcript.txt").write_text("\n".join(lines), encoding="utf-8")
    return transcript_json


def detect_scene_cuts(ffmpeg_bin: str, video_path: Path, threshold: float = 0.28) -> list[float]:
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-i",
            str(video_path),
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    cuts = []
    for m in re.finditer(r"pts_time:(\d+(?:\.\d+)?)", proc.stderr):
        t = round(float(m.group(1)), 2)
        if not cuts or (t - cuts[-1]) >= 0.4:
            cuts.append(t)
    return cuts


def sample_timestamps(duration: float, cuts: list[float], max_frames: int = 24) -> list[float]:
    if not duration or duration <= 0:
        return [0.0]

    pts = [0.2]
    if duration > 1.5:
        pts.append(1.5)
    if duration > 3.0:
        pts.append(3.0)

    step = max(2.0, duration / max(8, min(max_frames, 16)))
    t = step
    while t < duration - 0.5:
        pts.append(round(t, 2))
        t += step
    pts.append(round(max(0.2, duration - 0.5), 2))

    for c in cuts:
        cand = round(min(duration - 0.2, c + 0.2), 2)
        if all(abs(cand - p) >= 1.2 for p in pts):
            pts.append(cand)

    pts = sorted(set(pts))
    if len(pts) > max_frames:
        head = [p for p in pts if p <= 3.1]
        tail = [pts[-1]]
        mid = [p for p in pts if 3.1 < p < pts[-1]]
        keep_mid = max(1, max_frames - len(head) - len(tail))
        if len(mid) > keep_mid:
            stride = len(mid) / keep_mid
            mid = [mid[int(i * stride)] for i in range(keep_mid)]
        pts = sorted(set(head + mid + tail))
    return pts


def extract_frames_and_storyboards(
    ffmpeg_bin: str,
    video_path: Path,
    timestamps: list[float],
    out_dir: Path,
) -> list[str]:
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_records = []

    for idx, ts in enumerate(timestamps, 1):
        fp = frames_dir / f"frame_{idx:02d}_{ts:06.2f}s.jpg"
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{ts:.2f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(fp),
            ],
            check=False,
        )
        if fp.exists() and fp.stat().st_size > 0:
            frame_records.append((ts, fp))

    if not frame_records:
        return []

    per_sheet = 12
    cols = 4
    sample_img = Image.open(frame_records[0][1])
    sw, sh = sample_img.size
    target_w = 320
    target_h = max(180, int(sh * (target_w / sw)))
    pad = 12
    label_h = 26
    font = load_font(16)

    sheet_paths = []
    total_sheets = math.ceil(len(frame_records) / per_sheet)
    for s_idx in range(total_sheets):
        chunk = frame_records[s_idx * per_sheet : (s_idx + 1) * per_sheet]
        rows = math.ceil(len(chunk) / cols)
        canvas_w = cols * target_w + (cols + 1) * pad
        canvas_h = rows * (target_h + label_h) + (rows + 1) * pad
        canvas = Image.new("RGB", (canvas_w, canvas_h), (18, 18, 22))
        draw = ImageDraw.Draw(canvas)

        for i, (ts, fp) in enumerate(chunk):
            r, c = divmod(i, cols)
            x = pad + c * (target_w + pad)
            y = pad + r * (target_h + label_h + pad)
            im = Image.open(fp).convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
            canvas.paste(im, (x, y))
            tag = f"#{s_idx * per_sheet + i + 1:02d}  {format_ts(ts)}"
            draw.rectangle([x, y + target_h, x + target_w, y + target_h + label_h], fill=(32, 34, 42))
            draw.text((x + 8, y + target_h + 4), tag, fill=(240, 240, 245), font=font)

        sheet_path = out_dir / f"storyboard_{s_idx + 1}.jpg"
        canvas.save(sheet_path, quality=88)
        sheet_paths.append(str(sheet_path))

    return sheet_paths


def extract_subtitle_verify_sheet(
    ffmpeg_bin: str, video_path: Path, timestamps: list[float], out_dir: Path
) -> str | None:
    """按指定秒数批量截取画面中下部字幕带并拼成精校图，专用于校正方言/谐音误转。"""
    sub_dir = out_dir / "verify_subs"
    sub_dir.mkdir(parents=True, exist_ok=True)
    crops = []
    for i, ts in enumerate(timestamps, 1):
        fp = sub_dir / f"sub_{i:02d}_{ts:06.2f}s.jpg"
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{ts:.2f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "4",
                str(fp),
            ],
            check=False,
        )
        if fp.exists() and fp.stat().st_size > 0:
            im = Image.open(fp).convert("RGB")
            w, h = im.size
            crop = im.crop((0, int(h * 0.55), w, int(h * 0.88))).resize(
                (360, 190), Image.Resampling.LANCZOS
            )
            draw = ImageDraw.Draw(crop)
            draw.rectangle([0, 0, 88, 22], fill=(0, 0, 0))
            draw.text((5, 4), f"{format_ts(ts)}", fill=(255, 240, 80))
            crops.append(crop)

    if not crops:
        return None

    cols = 4
    rows = math.ceil(len(crops) / cols)
    sheet = Image.new("RGB", (cols * 360, rows * 190), (20, 20, 24))
    for i, c_im in enumerate(crops):
        r, c = divmod(i, cols)
        sheet.paste(c_im, (c * 360, r * 190))

    sheet_path = sub_dir / "subs_verify.jpg"
    sheet.save(sheet_path, quality=88)
    return str(sheet_path)


def main():
    parser = argparse.ArgumentParser(description="提取抖音视频逐字稿与分镜故事板")
    parser.add_argument("video", nargs="?", help="本地视频文件路径（与 --share-text / --video-url 三选一）")
    parser.add_argument("--share-text", default="", help="抖音分享口令或短链（自动走移动端公开分享页解析并下载）")
    parser.add_argument("--video-url", default="", help="已捕获的视频流 URL（将自动下载并封装）")
    parser.add_argument("--audio-url", default="", help="可选：分离音频流 URL（与 --video-url 配合合并）")
    parser.add_argument("--out-dir", required=True, help="输出目录")
    parser.add_argument("--model", default="", help="自定义 Whisper 模型名（留空自动匹配当前后端）")
    parser.add_argument("--max-frames", type=int, default=24, help="最多抽取关键帧数量")
    parser.add_argument("--skip-asr", action="store_true", help="跳过语音转写（仅抽帧与元信息）")
    parser.add_argument(
        "--sub-times",
        default="",
        help="仅执行方言字幕带精校模式：逗号分隔的秒数列表，如 '7.5,14.5,44.5'",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = find_ffmpeg()

    douyin_meta = None
    if args.share_text.strip():
        douyin_meta = parse_douyin_share_page(args.share_text.strip())
        video_path = out_dir / f"douyin_{douyin_meta['video_id']}.mp4"
        download_media(
            douyin_meta["play_url"],
            video_path,
            referer="https://www.iesdouyin.com/",
            user_agent=MOBILE_UA,
        )
    elif args.video_url.strip():
        video_path = download_and_mux(
            ffmpeg_bin, args.video_url.strip(), args.audio_url.strip() or None, out_dir
        )
    elif args.video:
        video_path = Path(args.video).expanduser().resolve()
        if not video_path.exists():
            print(json.dumps({"ok": False, "error": f"视频文件不存在: {video_path}"}, ensure_ascii=False))
            sys.exit(1)
    else:
        print(json.dumps({"ok": False, "error": "必须提供本地视频路径、--share-text 或 --video-url"}, ensure_ascii=False))
        sys.exit(1)

    if args.sub_times.strip():
        ts_vals = [float(x.strip()) for x in args.sub_times.split(",") if x.strip()]
        sheet = extract_subtitle_verify_sheet(ffmpeg_bin, video_path, ts_vals, out_dir)
        print(json.dumps({"ok": bool(sheet), "subs_verify_sheet": sheet, "count": len(ts_vals)}, ensure_ascii=False))
        return

    meta = probe_video(ffmpeg_bin, video_path)
    cuts = detect_scene_cuts(ffmpeg_bin, video_path)
    avg_cut = round(meta["duration_sec"] / max(1, len(cuts) + 1), 2) if meta["duration_sec"] else None

    timestamps = sample_timestamps(meta["duration_sec"] or 0.0, cuts, max_frames=args.max_frames)
    storyboards = extract_frames_and_storyboards(ffmpeg_bin, video_path, timestamps, out_dir)

    transcript_info = None
    if not args.skip_asr and meta.get("audio_codec"):
        wav_path = out_dir / "audio_16k.wav"
        extract_audio(ffmpeg_bin, video_path, wav_path)
        transcript_info = transcribe_audio(wav_path, args.model.strip() or None, out_dir)
        try:
            wav_path.unlink()
        except OSError:
            pass

    summary = {
        "ok": True,
        "ffmpeg_bin": ffmpeg_bin,
        "douyin_meta": douyin_meta,
        "meta": meta,
        "editing": {
            "detected_scene_cuts": len(cuts),
            "scene_cut_timestamps": cuts,
            "avg_shot_duration_sec": avg_cut,
        },
        "sampled_frame_count": len(timestamps),
        "storyboards": storyboards,
        "transcript_txt": str(out_dir / "transcript.txt") if transcript_info else None,
        "transcript_json": str(out_dir / "transcript.json") if transcript_info else None,
        "asr_backend": transcript_info["backend"] if transcript_info else None,
        "segment_count": transcript_info["segment_count"] if transcript_info else 0,
    }
    (out_dir / "assets_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
