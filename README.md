# 小憩 | SRT to Sketch Animation

**一款本地运行的 SRT 字幕分镜与白板动画工作流工具。** 从字幕导入、场景拆分、分镜生成和审核，到手绘动画或静态画面合成，在一个浏览器界面中完成。

**A local-first workflow for turning SRT subtitles into storyboards and whiteboard-style videos.** Import subtitles, split scenes, generate and review storyboards, then render hand-drawn animation or compose still images into a video from one browser-based workspace.

[中文](#中文) · [English](#english)

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

## 中文

### 项目简介

小憩面向知识讲解、教程、播客和短视频创作者。它把字幕当作视频时间轴的事实来源：保留每条 SRT 字幕的时间戳，将内容整理成可编辑场景，再把场景画面、字幕与音轨组合成视频。应用由 Python 本地服务和静态网页界面组成，不需要云端账号或数据库。

### 项目特点

- **SRT 驱动的场景工作流**：导入 SRT，检查字幕时间与内容，并按场景整理；可选文本模型辅助理解语义和拆幕。
- **分镜可审核、可编辑**：逐幕检查场景、提示词和画面，调整后再继续制作。
- **两种制作路径**：选择逐幕手绘动画，或按 SRT 原始时间轴将静态画面直接合成为视频。
- **可配置的生成服务**：分别设置 OpenAI 兼容的文本与图像 API，测试连接并选择模型。
- **可复用的视觉资产**：组织角色、道具、场景和自定义参考；内置 handraw-style 画风目录及本地字体。
- **字幕和音频控制**：调整字幕样式、位置和字体；导入音频并按字幕时间轴对齐。
- **本地优先与隐私**：界面默认只监听 `127.0.0.1`；API 密钥和项目数据保存在本机，不进入 Git 仓库。

### 工作流程

1. 启动应用，在项目页创建项目并导入 `.srt` 文件。
2. 在字幕和拆幕设置中检查字幕，配置场景拆分选项；需要时可配置文本 API 辅助语义拆分。
3. 检查拆分结果，进入分镜页生成或编辑画面，并逐幕审核。
4. 选择手绘动画或静态画面直合成路径；按需导入音轨、调整字幕及输出选项。
5. 开始渲染并在项目目录中查看导出视频。

界面会显示当前阶段和可用操作。API 生成与视频渲染可能产生费用或消耗本机资源，请先检查所选服务和输出设置。

### 系统要求

- Python 3.10 或更高版本（推荐 Python 3.12）。
- Pillow 10 或更高版本，用于部分图像处理功能。
- FFmpeg 与 FFprobe，并配置在系统 `PATH` 中，才能导出视频。
- 可选：OpenAI 兼容的文本 API 和图像生成 API，以及对应的 API Key。
- Windows、macOS 或 Linux。下面的命令示例以 Windows PowerShell 为主。

### 安装与启动

#### Windows（PowerShell）

```powershell
git clone https://github.com/ohmoneymoneygohome/srt-to-sketch-animation.git
cd srt-to-sketch-animation
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python server.py
```

如果 PowerShell 阻止激活虚拟环境，也可以直接调用虚拟环境里的 Python：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

#### macOS / Linux

```bash
git clone https://github.com/ohmoneymoneygohome/srt-to-sketch-animation.git
cd srt-to-sketch-animation
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

启动后打开 <http://127.0.0.1:8766>。停止服务时，在终端按 `Ctrl+C`。

如端口 `8766` 已被占用，可以改用其他本地端口：

```powershell
$env:WORKFLOW_PORT = "8767"
python server.py
```

macOS / Linux：

```bash
WORKFLOW_PORT=8767 python server.py
```

### 首次配置

1. 打开首页的设置页。
2. 按需填写文本 API 与图像 API 的 Base URL、模型和 API Key。服务应提供 OpenAI 兼容接口；文本模型和图像模型可以来自不同服务。
3. 使用“测试连接”或模型列表检查配置，再保存。
4. 如果要导出视频，先安装 FFmpeg，并确认 `ffmpeg` 与 `ffprobe` 都能从终端调用。

密钥保存在根目录的 `config.local.json`。该文件已由 `.gitignore` 排除，不要将其提交到 GitHub。`.env.example` 仅供可选的脚本配置参考，当前图形设置页将配置保存到本地 JSON 文件。

### FFmpeg 安装提示

请根据操作系统从可信来源安装 FFmpeg，并将 `ffmpeg`、`ffprobe` 添加到 `PATH`。可在终端验证：

```text
ffmpeg -version
ffprobe -version
```

本项目不打包 FFmpeg 二进制文件。视频编解码支持取决于你安装的 FFmpeg 构建和系统环境。

### 数据位置与隐私

- 应用设置：仓库根目录 `config.local.json`。
- 项目和生成文件：默认保存在仓库根目录 `projects/`，可在设置中更改项目根目录。
- `.gitignore` 会排除 `config.local.json`、`.env`、`projects/`、生成媒体及 Python 缓存。
- API Key 会发送到你配置的 API 服务；提交文本、字幕、参考图或音频前，请确认你已获准将这些内容提供给该服务。
- 服务默认绑定 `127.0.0.1`，仅供本机访问。不要在不理解访问控制与网络暴露风险时将服务改为监听公网或局域网地址。

### 故障排查

- **页面无法打开**：确认启动终端仍在运行，地址和端口与终端输出一致；如端口冲突，设置 `WORKFLOW_PORT`。
- **AI 连接失败**：检查 Base URL 是否包含正确的服务地址、API Key 是否有效、所选模型是否支持对应接口，以及网络是否可达。
- **找不到 FFmpeg / FFprobe**：安装二者并将其目录加入 `PATH`，然后重启应用，再检查导出页状态。
- **字体或画风素材缺失**：确认 `static/qiaomu-cover-fonts/` 和 `static/handraw-style/` 目录随仓库完整保留。
- **Windows 无法激活虚拟环境**：使用上面的 `.venv\Scripts\python.exe` 直接运行命令。

### 开发与检查

```bash
python -m py_compile server.py
```

运行服务：

```bash
python server.py
```

目前仓库未附带独立自动化测试套件。改动服务端后至少运行语法检查，并在浏览器中检查首页及相关工作流页面。

### 第三方资源与许可

小憩自有代码按 [MIT License](LICENSE) 发布。内置的 `handraw-style` 画风库、乔木封面字库及其素材受各自许可证约束；再分发时请保留对应许可和署名。详情见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及各资源目录中的许可文件。FFmpeg 不随项目分发，需单独安装并遵守其适用许可。

### 参与贡献

欢迎通过 GitHub Issues 报告问题或提出功能建议，也欢迎提交 Pull Request。提交前请确认：

- 没有加入 API Key、个人字幕、音频、参考图片或生成视频。
- 新增第三方资源附带了来源和许可说明。
- 服务端通过 `python -m py_compile server.py`。

## English

### Overview

Xiaoqi (小憩) is designed for educational videos, tutorials, podcasts, and short-form creators. Subtitles are treated as the source of truth for the video timeline: SRT timestamps are preserved while captions are organized into editable scenes, then combined with visuals and audio. The app consists of a local Python server and a static web interface; it requires no hosted account or database.

### Features

- **SRT-based scene workflow**: Import SRT files, inspect subtitle timing and text, and organize captions into scenes. An optional text model can help interpret content and split scenes.
- **Reviewable storyboards**: Inspect and edit scenes, prompts, and images before continuing.
- **Two production paths**: Create scene-by-scene hand-drawn animation or compose still images into a video using the original SRT timeline.
- **Configurable generation services**: Configure separate OpenAI-compatible text and image APIs, test connections, and select models.
- **Reusable visual assets**: Organize characters, props, scenes, and custom references. The repository includes the handraw-style catalog and local font assets.
- **Subtitle and audio controls**: Adjust subtitle style, placement, and font; import audio and align it to the subtitle timeline.
- **Local-first privacy**: The interface binds to `127.0.0.1` by default. API keys and project files stay on your machine and are excluded from Git.

### Workflow

1. Start the app, create a project, and import an `.srt` file from the project page.
2. Review subtitles and scene-splitting options. Configure a text API for optional semantic scene splitting.
3. Review the resulting scenes, generate or edit storyboard images, and approve each scene.
4. Choose hand-drawn animation or still-image composition. Import audio and adjust subtitle and output options as needed.
5. Render the video and find the exported file in the project directory.

The interface shows the current stage and available actions. API generation may incur charges, and video rendering uses local compute resources; review your provider and output settings before starting.

### Requirements

- Python 3.10 or newer (Python 3.12 recommended).
- Pillow 10 or newer for selected image-processing features.
- FFmpeg and FFprobe on `PATH` for video export.
- Optional: OpenAI-compatible text and image-generation APIs and your own API keys.
- Windows, macOS, or Linux. The commands below include PowerShell examples for Windows.

### Installation and launch

#### Windows (PowerShell)

```powershell
git clone https://github.com/ohmoneymoneygohome/srt-to-sketch-animation.git
cd srt-to-sketch-animation
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python server.py
```

If PowerShell prevents virtual-environment activation, call its Python executable directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

#### macOS / Linux

```bash
git clone https://github.com/ohmoneymoneygohome/srt-to-sketch-animation.git
cd srt-to-sketch-animation
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

When the server starts, open <http://127.0.0.1:8766>. Press `Ctrl+C` in the terminal to stop it.

If port `8766` is already in use, select another local port:

```powershell
$env:WORKFLOW_PORT = "8767"
python server.py
```

On macOS / Linux:

```bash
WORKFLOW_PORT=8767 python server.py
```

### First-time setup

1. Open the settings page from the home screen.
2. Enter the Base URL, model, and API key for the text and image APIs as needed. The services must expose OpenAI-compatible endpoints; text and image models may come from different providers.
3. Use the connection test or model list to verify the settings, then save.
4. To export video, install FFmpeg and make sure both `ffmpeg` and `ffprobe` are available from the terminal.

Keys are saved in `config.local.json` at the repository root. This file is excluded by `.gitignore`; never commit it to GitHub. `.env.example` is provided as an optional reference for scripts, while the current settings UI stores configuration in the local JSON file.

### Installing FFmpeg

Install FFmpeg from a trusted source for your operating system and add the directories containing `ffmpeg` and `ffprobe` to `PATH`. Verify them in a terminal:

```text
ffmpeg -version
ffprobe -version
```

FFmpeg binaries are not bundled with this project. Codec support depends on your FFmpeg build and operating system.

### Data and privacy

- App settings: `config.local.json` in the repository root.
- Projects and generated files: stored in `projects/` by default. You can change the project root in settings.
- `.gitignore` excludes `config.local.json`, `.env` files, `projects/`, generated media, and Python caches.
- API keys are sent to the API service you configure. Before submitting text, subtitles, reference images, or audio, make sure you are allowed to share that content with the service.
- The server binds to `127.0.0.1` by default for local access. Do not expose it to a public or local network unless you understand the access-control and network-security implications.

### Troubleshooting

- **The page does not load**: Make sure the server is still running and use the address and port printed in the terminal. Set `WORKFLOW_PORT` if the port is occupied.
- **An AI connection fails**: Check the Base URL, API key, model support for the requested endpoint, and network connectivity.
- **FFmpeg or FFprobe is missing**: Install both tools, add them to `PATH`, restart the app, and check the export page again.
- **Fonts or style assets are missing**: Make sure `static/qiaomu-cover-fonts/` and `static/handraw-style/` are present in the checkout.
- **Virtual-environment activation fails on Windows**: Run commands through `.venv\Scripts\python.exe` as shown above.

### Development and checks

```bash
python -m py_compile server.py
```

Start the app with:

```bash
python server.py
```

The repository does not currently include a standalone automated test suite. After server-side changes, run the syntax check and verify the home page and affected workflow screens in a browser.

### Third-party assets and licensing

Original Xiaoqi code is released under the [MIT License](LICENSE). The bundled `handraw-style` catalog, Qiaomu Cover Fonts, and their assets are subject to their own licenses. Keep their license and attribution files when redistributing; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the license files in each asset directory. FFmpeg is distributed separately and remains subject to its applicable license.

### Contributing

Bug reports, feature requests, and pull requests are welcome through GitHub Issues. Before submitting a change, make sure that:

- It contains no API keys, personal subtitles, audio, reference images, or rendered videos.
- New third-party assets include source and license information.
- The server passes `python -m py_compile server.py`.
