#!/usr/bin/env python3
"""
douyin-video-deconstruct 跨平台环境自检与全自动依赖安装脚本。
特性：
- 幂等：本机已具备 ffmpeg + Pillow + playwright + (mlx_whisper 或 faster_whisper) 时 1 秒通过，不重复下载。
- 跨平台浏览器驱动：自动安装 playwright；优先直接复用本机已安装的 Google Chrome / Microsoft Edge，
  若机器上两者均未安装才自动下载 Playwright Chromium。
- 跨平台语音转写：
  * Apple Silicon Mac (Darwin arm64)：优先使用/安装 mlx-whisper（失败自动回退 faster-whisper）
  * Intel Mac / Windows / Linux：优先使用/安装 faster-whisper
- 免管理员权限：缺失 Python 依赖时在 Skill 目录内建独立 .venv，避免触发系统 PEP 668 限制；
  缺失系统 ffmpeg 时优先尝试 Homebrew（macOS），失败则自动安装 imageio-ffmpeg 内置静态二进制兜底。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
VENV_DIR = SKILL_DIR / ".venv"


def get_venv_python() -> Path:
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python3"


def find_system_ffmpeg() -> str | None:
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
    return None


def detect_system_browser() -> str | None:
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


def probe_python_env(py_bin: str) -> dict:
    code = r"""
import importlib.util, json
def has(mod):
    return importlib.util.find_spec(mod) is not None
ff = None
if has("imageio_ffmpeg"):
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ff = None
print(json.dumps({
    "PIL": has("PIL"),
    "playwright": has("playwright"),
    "mlx_whisper": has("mlx_whisper"),
    "faster_whisper": has("faster_whisper"),
    "imageio_ffmpeg": ff
}))
"""
    proc = subprocess.run([py_bin, "-c", code], capture_output=True, text=True)
    if proc.returncode != 0:
        return {
            "PIL": False,
            "playwright": False,
            "mlx_whisper": False,
            "faster_whisper": False,
            "imageio_ffmpeg": None,
        }
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {
            "PIL": False,
            "playwright": False,
            "mlx_whisper": False,
            "faster_whisper": False,
            "imageio_ffmpeg": None,
        }


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine().lower() in ("arm64", "aarch64")


def check_ready(py_bin: str) -> tuple[bool, dict]:
    mods = probe_python_env(py_bin)
    ff = find_system_ffmpeg() or mods.get("imageio_ffmpeg")
    asr = "mlx_whisper" if mods.get("mlx_whisper") else ("faster_whisper" if mods.get("faster_whisper") else None)
    browser = detect_system_browser()
    ready = bool(ff and mods.get("PIL") and mods.get("playwright") and asr)
    return ready, {
        "python_bin": str(py_bin),
        "ffmpeg_bin": ff,
        "asr_backend": asr,
        "browser_channel": browser or "playwright-chromium",
        "modules": mods,
    }


def ensure_venv() -> Path:
    venv_py = get_venv_python()
    if not venv_py.exists():
        # --system-site-packages 可直接复用本机已装好的 mlx-whisper / Pillow，免重复下载大包
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(VENV_DIR)], check=True)
    return venv_py


def try_brew_install_ffmpeg() -> str | None:
    if platform.system() != "Darwin":
        return None
    brew = shutil.which("brew") or ("/opt/homebrew/bin/brew" if os.path.isfile("/opt/homebrew/bin/brew") else None)
    if not brew:
        return None
    proc = subprocess.run([brew, "install", "ffmpeg"], capture_output=True, text=True)
    if proc.returncode == 0:
        return find_system_ffmpeg()
    return None


def main():
    parser = argparse.ArgumentParser(description="自动检测并安装 douyin-video-deconstruct 所需依赖")
    parser.add_argument("--check-only", action="store_true", help="仅检查环境，不执行安装")
    parser.add_argument("--force-venv", action="store_true", help="强制在 Skill 目录内创建/使用独立 .venv")
    args = parser.parse_args()

    actions = []

    candidate_pys = []
    venv_py = get_venv_python()
    if venv_py.exists():
        candidate_pys.append(str(venv_py))
    if not args.force_venv:
        candidate_pys.append(sys.executable)

    for py in candidate_pys:
        ready, info = check_ready(py)
        if ready:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "status": "ready",
                        "platform": f"{platform.system()}-{platform.machine()}",
                        "actions": actions,
                        **info,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

    if args.check_only:
        _, info = check_ready(candidate_pys[0])
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "missing_dependencies",
                    "platform": f"{platform.system()}-{platform.machine()}",
                    **info,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(1)

    target_py = str(ensure_venv())
    actions.append(f"prepared_venv:{VENV_DIR}")

    _, info = check_ready(target_py)
    mods = info["modules"]
    pkgs_to_install = []

    if not mods.get("PIL"):
        pkgs_to_install.append("Pillow")

    if not mods.get("playwright"):
        pkgs_to_install.append("playwright")

    if not mods.get("mlx_whisper") and not mods.get("faster_whisper"):
        if is_apple_silicon():
            pkgs_to_install.append("mlx-whisper")
        else:
            pkgs_to_install.append("faster-whisper")

    ff = find_system_ffmpeg() or mods.get("imageio_ffmpeg")
    if not ff:
        brew_ff = try_brew_install_ffmpeg()
        if brew_ff:
            actions.append(f"brew_installed_ffmpeg:{brew_ff}")
            ff = brew_ff
        else:
            pkgs_to_install.append("imageio-ffmpeg")

    if pkgs_to_install:
        subprocess.run(
            [target_py, "-m", "pip", "install", "--upgrade", "pip"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        proc = subprocess.run(
            [target_py, "-m", "pip", "install", *pkgs_to_install],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            if "mlx-whisper" in pkgs_to_install:
                fallback_pkgs = [p if p != "mlx-whisper" else "faster-whisper" for p in pkgs_to_install]
                proc2 = subprocess.run(
                    [target_py, "-m", "pip", "install", *fallback_pkgs],
                    capture_output=True,
                    text=True,
                )
                if proc2.returncode != 0:
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "status": "install_failed",
                                "packages": fallback_pkgs,
                                "stderr": proc2.stderr[-1200:],
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    sys.exit(1)
                actions.append(f"pip_installed:{','.join(fallback_pkgs)}")
            else:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "status": "install_failed",
                            "packages": pkgs_to_install,
                            "stderr": proc.stderr[-1200:],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                sys.exit(1)
        else:
            actions.append(f"pip_installed:{','.join(pkgs_to_install)}")

    # 若机器上未安装 Google Chrome 或 Edge，自动安装 Playwright Chromium 兜底
    if not detect_system_browser():
        p_chr = subprocess.run(
            [target_py, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
        )
        if p_chr.returncode == 0:
            actions.append("playwright_installed:chromium")

    ready, final_info = check_ready(target_py)
    print(
        json.dumps(
            {
                "ok": ready,
                "status": "installed" if ready else "incomplete",
                "platform": f"{platform.system()}-{platform.machine()}",
                "actions": actions,
                **final_info,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not ready:
        sys.exit(1)


if __name__ == "__main__":
    main()
