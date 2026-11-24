---
name: douyin-video-deconstruct
description: 将抖音短视频链接或本地视频文件拆解为标准化的 11 字段“爆款模版拆解卡”（模版名称、模版类型、模版选题、样片来源及数据、所属赛道、呈现方式、模版结构、逐句拆片演示、模版优点、注意事项、5 条选题裂变）。当用户发送抖音分享链接/口令或本地短视频文件，要求“拆片”“拆一下这条视频”“按模版拆解”，或刚安装本 Skill 准备初始化环境时触发。自动检测并安装所需的 ffmpeg、Pillow、Playwright（优先直接驱动本机已安装的 Chrome/Edge）与离线语音转写引擎，不依赖宿主内置浏览器（OpenClaw/小龙虾/Codex/Claude Code/Antigravity 全通用），严格按 11 字段标准模版卡及清爽 Markdown 排版输出，不附加多余板块或改写脚本（除非用户明确要求）。
---

# 抖音短视频模版卡拆解（Douyin Video Deconstruct）

本 Skill 用于将一条抖音短视频（分享口令/链接或本地文件）全自动拆解为可直接归档复用的标准 11 字段短视频模版拆解卡，自带跨平台自动装依赖脚本与本机 Chrome 持久化驱动，**不依赖宿主是否自带浏览器**。

> 约定：下文中 `<skill_dir>` 指本 `SKILL.md` 所在的目录路径（由宿主 Agent 根据实际加载路径自动填入，不写死任何固定用户目录）。

## 核心原则

1. **自动装环境、不挑宿主**：安装本 Skill 后或首次拆片前，自动运行 `scripts/setup_env.py` 一键安装缺失的 `ffmpeg`、`Pillow`、`playwright` 和离线语音转写引擎；通过 `scripts/fetch_with_chrome.py` 直接调用本机真实 Chrome/Edge 的独立持久化配置（不抢用户日常浏览器），无内置浏览器的宿主也能一条命令跑通。
2. **严格还原 11 字段与清爽 Markdown 排版**：严格按 [`references/deconstruct-framework.md`](references/deconstruct-framework.md) 输出 11 个固定字段；字段名与拆片模块名加粗独立分段，逐句台词使用 Markdown 硬换行（行末双空格 `  `），**绝不出现单换行粘连成一坨文字**。默认不附加多余分析板块或改写脚本（除非用户明确要求）。
3. **证据优先 + 原片字幕双核精校**：`拆片演示` 必须来自真实转写逐字稿，遇方言/口音/谐音误差时必须对照抽帧画面中的原片字幕精校；`呈现方式` 必须基于真实故事板画面判断；公开数据必须来自真实页面，禁止虚构。
4. **合规边界**：遇到不可关闭的强制登录墙或人机验证（CAPTCHA）时，保持可见 Chrome 窗口请用户亲自完成扫码或验证，**绝不绕过验证码或破解权限控制**；用户完成一次登录后，登录态自动持久化保存在独立 Profile 中，后续拆片免反复登录。

---

## 执行步骤

### 第 0 步：环境自检与所需软件自动安装（首次或安装后自动执行）

在每次拆片前（或用户刚安装完本 Skill 要求初始化时），先运行环境自检/自动安装脚本：

```bash
python3 "<skill_dir>/scripts/setup_env.py"
```

- **本机依赖已齐**：1 秒返回 `{"ok": true, "status": "ready", "python_bin": "...", "ffmpeg_bin": "...", "asr_backend": "...", "browser_channel": "..."}`。
- **缺少任意依赖**：脚本自动在 `<skill_dir>/.venv` 隔离环境（复用已有系统大包、免管理员权限）中装齐 `Pillow`、`playwright`、`mlx-whisper`（Apple Silicon）或 `faster-whisper`（其他平台）、`ffmpeg`（优先系统/Homebrew，缺省用 `imageio-ffmpeg` 兜底）；优先复用本机已安装的 Chrome/Edge，二者均无时才下载 Playwright Chromium。
- 记住 JSON 返回的 `python_bin`（记为 `<py_bin>`），后续命令统一使用 `<py_bin>` 执行。

---

### 第 1 步：获取本地视频文件与公开元数据

- **情况 A：用户提供本地视频路径（`.mp4` / `.mov` / `.mkv` 等）**
  - 确认文件存在，直接跳到第 2 步。
- **情况 B：用户提供抖音链接或分享口令（含 `v.douyin.com` / `douyin.com/video/...`）**
  - 直接运行本 Skill 自带的**本机真实 Chrome 持久化抓取脚本**（自动复用本机 Chrome/Edge，登录态保存在独立目录 `~/.douyin-deconstruct-chrome-profile`，不抢日常浏览器）：

```bash
"<py_bin>" "<skill_dir>/scripts/fetch_with_chrome.py" \
  "<抖音分享口令或完整链接>" \
  --out-dir "<输出目录>" \
  --wait-login 120
```

  - **若正常放行播放**：脚本自动捕获音视频流并无损合并为 `<输出目录>/source_video.mp4`，同时输出 `browser_fetch.json`（含最终 URL、抖音账号、发布时间、标题、点赞/评论/收藏/转发数）。
  - **若首次使用弹出强制登录窗或人机验证**：脚本会保持可见的 Chrome 窗口打开等待用户亲自扫码/完成验证；登录成功后会自动保存登录态并立刻继续下载视频（后续再拆无需重登）。若超时仍未完成（退出码 2），如实告知用户重新执行并在弹出窗口完成一次验证/登录即可（绝不硬编文案）。

---

### 第 2 步：运行本地抽帧与离线转写脚本

使用 `<py_bin>` 对第 1 步得到的 `<输出目录>/source_video.mp4`（或用户提供的本地视频路径）执行离线转写与分镜故事板生成：

```bash
"<py_bin>" "<skill_dir>/scripts/extract_video_assets.py" \
  "<本地视频路径>" \
  --out-dir "<输出目录>"
```

输出文件：`assets_summary.json`、`transcript.txt`、`transcript.json`、`storyboard_1.jpg`... 及 `frames/`。

---

### 第 3 步：核验逐字稿、方言字幕精校与呈现方式

1. 读取 `<输出目录>/transcript.txt`、`assets_summary.json` 与 `browser_fetch.json`（如有）。
2. 逐张查看 `<输出目录>/storyboard_*.jpg` 分镜故事板，确认真实的 `呈现方式`（如 `Vlog`、`口播`、`Vlog+场景化对话`、`场景化口播`、`画面配音` 等）。
3. **方言/谐音精校**：若逐字稿中存在疑似方言、谐音或专有名词误转，调用 `--sub-times` 模式截取对应秒数的原片字幕带拼图核对并校正：

```bash
"<py_bin>" "<skill_dir>/scripts/extract_video_assets.py" \
  "<本地视频路径>" \
  --out-dir "<输出目录>" \
  --sub-times "7.5,14.5,44.5,93.0,151.0"
```

---

### 第 4 步：输出标准 11 字段模版拆解卡

1. 完整读取 [`references/deconstruct-framework.md`](references/deconstruct-framework.md)（严格遵守 11 字段格式与 Markdown 硬换行排版铁律）。
2. 严格按模块一一对应、短句硬换行输出最终拆片卡；输出前自检：
   - 11 个字段齐全且无多余板块；
   - `模版结构` 与 `拆片演示` 编号分段 100% 一一对应；
   - 每句台词末尾带两个空格或空行，Markdown 渲染绝不合并粘连；
   - `选题裂变` 5 条严格复用原 `模版选题` 句式骨架。
