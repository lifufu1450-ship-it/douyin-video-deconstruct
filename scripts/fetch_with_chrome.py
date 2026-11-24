#!/usr/bin/env python3
"""
douyin-video-deconstruct 本机真实 Chrome 持久化打开与媒体流获取脚本。
专为无内置浏览器的 Agent（如本地 OpenClaw/小龙虾、Codex、Claude Code 等）设计：
- 使用独立持久化 Profile 目录（~/.douyin-deconstruct-chrome-profile），绝不干扰或读取用户日常 Chrome 数据；
- 优先驱动本机已安装的 Google Chrome / Microsoft Edge（无需额外下载大型 Chromium）；
- 严守合规原则：如遇强制登录窗或人机验证（CAPTCHA），绝不绕过，保持可见窗口等待用户亲自完成，
  登录态会自动持久保存在该独立 Profile 中，后续拆片免反复登录。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE_DIR = Path.home() / ".douyin-deconstruct-chrome-profile"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


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
    try:
        import imageio_ffmpeg

        ff = imageio_ffmpeg.get_ffmpeg_exe()
        if ff and os.path.isfile(ff):
            return ff
    except Exception:
        pass
    raise RuntimeError("未找到 ffmpeg，请先运行 setup_env.py。")


def detect_browser_channel() -> str | None:
    sys_name = platform.system()
    if sys_name == "Darwin":
        if os.path.exists("/Applications/Google Chrome.app"):
            return "chrome"
        if os.path.exists("/Applications/Microsoft Edge.app"):
            return "msedge"
    elif sys_name == "Windows":
        for p in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ):
            if os.path.exists(p):
                return "chrome"
        for p in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ):
            if os.path.exists(p):
                return "msedge"
    else:
        if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
            return "chrome"
        if shutil.which("microsoft-edge"):
            return "msedge"
    return None


def extract_url(raw_text: str) -> str:
    m = re.search(r"https?://[^\s\"'<>]+", raw_text)
    if not m:
        raise RuntimeError("未在输入中找到有效链接。")
    return m.group(0)


def download_media(url: str, dest: Path) -> Path:
    req = urllib.request.Request(
        url,
        headers={
            "Referer": "https://www.douyin.com/",
            "User-Agent": BROWSER_UA,
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


def check_page_gate(page) -> tuple[bool, str]:
    """检测是否存在不可关闭的强制登录窗或人机验证（只读检测，绝不绕过）。"""
    try:
        state = page.evaluate(
            r"""() => {
            const txt = (document.body && document.body.innerText) || '';
            const hasCaptcha = /验证|滑块|拼图|安全验证|captcha/i.test(txt) &&
                !!document.querySelector('[class*="captcha"], [id*="captcha"], iframe[src*="verify"]');
            const loginPanel = document.querySelector('#login-pannel, [id*="douyin_login_comp"]');
            const panelVisible = !!(loginPanel && loginPanel.offsetParent !== null);
            const hasCloseBtn = !!(loginPanel && loginPanel.querySelector('[class*="close"], .dy-account-close'));
            const forcedLoginText = txt.includes('登录后即可观看更多精彩内容');
            const isForcedLogin = (panelVisible && !hasCloseBtn) || (forcedLoginText && !hasCloseBtn);
            return { hasCaptcha, isForcedLogin, panelVisible, hasCloseBtn };
        }"""
        )
    except Exception:
        return False, ""

    if state.get("hasCaptcha"):
        return True, "captcha"
    if state.get("panelVisible") and state.get("hasCloseBtn"):
        # 仅点击网站本身提供的标准关闭 X 按钮（普通可关闭提示框）
        try:
            page.locator('[class*="close"], .dy-account-close').first.click(timeout=1500)
        except Exception:
            pass
        return False, ""
    if state.get("isForcedLogin"):
        return True, "forced_login"
    return False, ""


def extract_page_metadata(page) -> dict:
    try:
        return page.evaluate(
            r"""() => {
            const url = location.href;
            const titleEl = document.querySelector('h1') || document.querySelector('[data-e2e="video-desc"]');
            const title = titleEl ? titleEl.innerText.trim() : document.title;
            const mainArea = document.querySelector('main, [data-e2e="video-detail"], #douyin-right-container') || document.body;
            let author = '';
            const authorCandidates = mainArea.querySelectorAll('[data-e2e="video-author-name"], [data-e2e="user-info"] a, a[href*="/user/"]');
            for (const el of authorCandidates) {
                let t = (el.innerText || '').trim().split('\n')[0].trim();
                t = t.replace(/认证徽章.*$/, '').trim();
                if (t && !['我的', '首页', '关注', '朋友', '推荐', '精选'].includes(t) && !t.startsWith('粉丝')) {
                    author = t;
                    break;
                }
            }
            const getCount = (sel) => {
                const el = document.querySelector(sel);
                return el ? (el.innerText || '').trim().split('\n')[0].trim() : '';
            };
            let digg = getCount('[data-e2e="video-player-digg"]') || getCount('[data-e2e="detail-video-like-num"]');
            let comment = getCount('[data-e2e="feed-comment-icon"]') || getCount('[data-e2e="detail-video-comment-num"]');
            let collect = getCount('[data-e2e="video-player-collect"]') || getCount('[data-e2e="detail-video-collect-num"]');
            let share = getCount('[data-e2e="video-player-share"]') || getCount('[data-e2e="detail-video-share-num"]');
            if (!digg && !comment) {
                // 兜底：从正文下方统计栏提取连续 4 个纯数字/带万的互动计数
                const nums = [];
                for (const sp of mainArea.querySelectorAll('span')) {
                    const txt = (sp.innerText || '').trim();
                    if (/^\d+(?:\.\d+)?[万wW]?$/.test(txt) && sp.children.length === 0) {
                        nums.push(txt);
                    }
                }
                if (nums.length >= 4) {
                    [digg, comment, collect, share] = nums.slice(0, 4);
                }
            }
            const timeEl = mainArea.querySelector('[data-e2e="detail-video-publish-time"]');
            const publishTime = timeEl ? timeEl.innerText.trim() : '';
            return { url, author, title, publishTime, stats: { digg, comment, collect, share } };
        }"""
        )
    except Exception:
        return {"url": page.url}


def main():
    parser = argparse.ArgumentParser(description="使用本机真实 Chrome 持久化会话获取抖音公开视频与元数据")
    parser.add_argument("input_text", help="抖音分享口令或视频链接")
    parser.add_argument("--out-dir", required=True, help="输出目录")
    parser.add_argument(
        "--wait-login",
        type=int,
        default=90,
        help="遇到强制登录或人机验证时，保留可见 Chrome 窗口等待用户手动完成的最长秒数（默认 90）",
    )
    parser.add_argument(
        "--profile-dir",
        default=str(PROFILE_DIR),
        help="独立持久化 Chrome 用户目录（默认 ~/.douyin-deconstruct-chrome-profile）",
    )
    args = parser.parse_args()

    target_url = extract_url(args.input_text)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    channel = detect_browser_channel()
    captured = {"video_urls": [], "audio_urls": [], "mp4_urls": []}

    def on_response(resp):
        try:
            u = resp.url
            if ("douyinvod.com" in u or "video/tos" in u) and resp.status in (200, 206):
                if "media-video-" in u:
                    if u not in captured["video_urls"]:
                        captured["video_urls"].append(u)
                elif "media-audio-" in u:
                    if u not in captured["audio_urls"]:
                        captured["audio_urls"].append(u)
                elif "mime_type=video_mp4" in u or ".mp4" in u:
                    if u not in captured["mp4_urls"]:
                        captured["mp4_urls"].append(u)
        except Exception:
            pass

    with sync_playwright() as p:
        launch_kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": False,
            "viewport": {"width": 1280, "height": 860},
        }
        if channel:
            launch_kwargs["channel"] = channel

        context = p.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.on("response", on_response)

        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        gated, reason = check_page_gate(page)
        if gated:
            msg = (
                "请在弹出的 Chrome 窗口中完成抖音扫码登录（登录一次后将自动保存）"
                if reason == "forced_login"
                else "请在弹出的 Chrome 窗口中手动完成人机验证"
            )
            print(f"[等待人工完成] {msg}（最长等待 {args.wait_login} 秒）...", file=sys.stderr, flush=True)
            deadline = time.time() + args.wait_login
            while time.time() < deadline:
                time.sleep(2)
                gated, reason = check_page_gate(page)
                if not gated:
                    # 登录后清空旧的未登录阶段预加载流，重新等待正常播放流
                    captured["video_urls"].clear()
                    captured["audio_urls"].clear()
                    captured["mp4_urls"].clear()
                    time.sleep(5)
                    break

        if gated:
            context.close()
            print(
                json.dumps(
                    {
                        "ok": False,
                        "status": "blocked_by_gate",
                        "reason": reason,
                        "message": "超时仍停留在登录或人机验证弹窗，已按合规原则停止。",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            sys.exit(2)

        # 确保视频加载到媒体流
        wait_media_until = time.time() + 12
        while time.time() < wait_media_until:
            if (captured["video_urls"] and captured["audio_urls"]) or captured["mp4_urls"]:
                break
            time.sleep(1)

        meta = extract_page_metadata(page)
        context.close()

    video_url = captured["video_urls"][-1] if captured["video_urls"] else (captured["mp4_urls"][-1] if captured["mp4_urls"] else None)
    audio_url = captured["audio_urls"][-1] if captured["audio_urls"] else None

    if not video_url:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "no_media_stream",
                    "meta": meta,
                    "message": "页面已打开但未捕获到媒体流，请确认视频已正常播放。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(1)

    ffmpeg_bin = find_ffmpeg()
    mp4_path = download_and_mux(ffmpeg_bin, video_url, audio_url, out_dir)
    result = {
        "ok": True,
        "browser_channel": channel or "chromium",
        "profile_dir": str(profile_dir),
        "video_path": str(mp4_path),
        "meta": meta,
    }
    (out_dir / "browser_fetch.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
