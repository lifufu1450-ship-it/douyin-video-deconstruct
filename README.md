# Douyin Video Deconstruct（抖音短视频全自动拆片 Skill）

一套面向 AI Agent（**Antigravity / OpenClaw 小龙虾 / Claude Code / Codex** 全通用）的开源抖音短视频拆片技能。

发一条抖音分享口令/链接或本地视频文件，Agent 自动完成：**自动装齐本地依赖 → 驱动本机真实 Chrome 持久化获取视频与公开数据 → 离线语音转写（免付费 API）→ 抽帧故事板 + 方言字幕精校 → 输出 11 字段标准化爆款模版拆解卡**。

---

## 给 AI 的一句话安装口令（复制发给任意 Agent）

```text
请帮我安装这个 GitHub 仓库里的 Skill：https://github.com/lifufu1450-ship-it/douyin-video-deconstruct
安装完成后，请运行里面的 scripts/setup_env.py 自动安装所需依赖环境。
```

### 或使用命令行一键安装（`skills.sh` / Codex / Claude Code）

```bash
npx -y skills@latest add lifufu1450-ship-it/douyin-video-deconstruct --global --yes
```

---

## 核心特性

- **不挑宿主、不依赖内置浏览器**：自带基于 Playwright 的本机 Chrome/Edge 持久化驱动（独立 Profile `~/.douyin-deconstruct-chrome-profile`，不干扰日常浏览器），无内置浏览器的 **OpenClaw（小龙虾）** 等宿主也能直接用。
- **零手动配环境**：自带 `scripts/setup_env.py`，首次运行自动在独立 `.venv` 中安装缺失依赖：
  - `ffmpeg`（优先复用系统/Homebrew，缺省自动安装 `imageio-ffmpeg` 免管理员权限内置静态二进制）
  - `Pillow`（生成 4×3 分镜故事板与字幕精校图）
  - `playwright`（优先直接调用本机已有 Chrome/Edge，免下大体积浏览器）
  - 离线语音转写引擎：Apple Silicon Mac 自动装 `mlx-whisper`（原生加速）；Intel Mac / Windows / Linux 自动装 `faster-whisper`
- **方言/口音双核精校**：支持 `--sub-times` 按秒截取画面原片烧录字幕拼图，自动校对方言谐音误转。
- **标准化 11 字段模版拆解卡**：清爽 Markdown 排版，输出 `模版名称`、`模版类型`、`模版选题`、`样片来源及数据`、`所属赛道`、`呈现方式`、`模版结构`、逐句 `拆片演示`、`模版优点`、`注意事项` 与 5 条同句式 `选题裂变`。
- **合规透明**：遇强制登录墙或人机验证（CAPTCHA）时保持可见浏览器窗口等待用户手动完成，绝不绕过验证码或破解权限控制；登录态持久保存，后续拆片免反复登录。

---

## 目录结构

```text
douyin-video-deconstruct/
├── SKILL.md                           # Agent Skill 主指令与全流程 SOP
├── references/
│   └── deconstruct-framework.md       # 11 字段模版拆解卡规范与通用结构词表
├── scripts/
│   ├── setup_env.py                   # 跨平台环境自检与自动依赖安装
│   ├── fetch_with_chrome.py           # 本机真实 Chrome 持久化获取视频与公开数据
│   └── extract_video_assets.py        # 离线转写、场景切镜、故事板与字幕带精校
├── LICENSE                            # MIT License
└── README.md
```

---

## 使用示例

安装好后，直接对你的 AI 说：

- `帮我拆下这条视频：<粘贴抖音分享口令或链接>`
- `拆一下这个本地视频：/path/to/video.mp4`

---

## 免责声明与许可

- 本项目仅用于个人短视频内容学习、脚本结构复盘与公开信息整理，请严格遵守目标平台服务条款与相关法律法规。
- 开源协议：[MIT License](LICENSE)
