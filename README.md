# 小憩 | SRT to Sketch Animation

**一款本地运行的 SRT 字幕分镜与白板动画工作流工具。** 从字幕导入、场景拆分、分镜生成和审核，到手绘动画或静态画面合成，在一个浏览器界面中完成。

**A local-first workflow for turning SRT subtitles into storyboards and whiteboard-style videos.** Import subtitles, split scenes, generate and review storyboards, then render hand-drawn animation or compose still images into a video from one browser-based workspace.

[中文](#中文) · [English](#english)

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

## 中文

### 目录

- [项目简介与来源](#项目简介与来源)
- [项目特点](#项目特点)
- [制作模式](#制作模式)
- [开始使用](#开始使用)
- [安装与启动](#安装与启动)
- [首次配置](#首次配置)
- [常见问题](#常见问题)
- [数据与隐私](#数据位置与隐私)
- [第三方资源与致谢](#第三方资源与致谢)

### 项目简介与来源

小憩面向知识讲解、教程、播客和短视频创作者。它把字幕当作视频时间轴的事实来源：保留每条 SRT 字幕的时间戳，将内容整理成可编辑场景，再把场景画面、字幕与音轨组合成视频。应用由 Python 本地服务和静态网页界面组成，不需要云端账号或数据库。

本项目在 [kaomei/stickman-video-director](https://github.com/kaomei/stickman-video-director) 的视频导演工作流基础上进行本地化和功能扩展。小憩面向 SRT 驱动的制作流程，重点扩展了逐条字幕时间轴校验、场景拆分和编辑、分镜审核、手绘动画与静态画面直合成两种出片路径、音频对齐，以及可复用的角色/道具/场景资产管理。上游项目的 README 和许可证请以其仓库为准；本项目保留独立的实现与许可说明。

### 项目特点

- **SRT 驱动的场景工作流**：导入 SRT，检查字幕时间与内容，并按场景整理；可选文本模型辅助理解语义和拆幕。
- **分镜可审核、可编辑**：逐幕检查场景、提示词和画面，调整后再继续制作。
- **两种制作路径**：选择逐幕手绘动画，或按 SRT 原始时间轴将静态画面直接合成为视频。
- **可配置的生成服务**：分别设置 OpenAI 兼容的文本与图像 API，测试连接并选择模型。
- **可复用的视觉资产**：组织角色、道具、场景和自定义参考；内置 handraw-style 画风目录及本地字体。
- **字幕和音频控制**：调整字幕样式、位置和字体；导入音频并按字幕时间轴对齐。
- **本地优先与隐私**：界面默认只监听 `127.0.0.1`；API 密钥和项目数据保存在本机，不进入 Git 仓库。

### 制作模式

| 模式 | 适用场景 | 处理方式 | 主要特点 |
| --- | --- | --- | --- |
| 手绘动画 | 希望画面逐步显现、呈现白板讲解效果 | 根据分镜逐场生成动画，再与字幕和音频合成 | 可逐幕审核和返工；渲染时间较长，并需要动画流程依赖 |
| 静态画面直合成 | 需要快速预览、轻量制作或已有静态分镜图 | 将每幕静态图按 SRT 起止时间放入视频时间轴 | 保留幕间停顿；流程较快，不经过逐幕动画 |

两种模式共用同一份字幕和场景数据。可在分镜审核后根据项目需要选择制作路径。

### 开始使用

1. 启动应用，在项目页创建项目并导入 `.srt` 文件。
2. 在字幕和拆幕设置中检查字幕，配置场景拆分选项；需要时可配置文本 API 辅助语义拆分。
3. 检查拆分结果，进入分镜页生成或编辑画面，并逐幕审核。
4. 选择手绘动画或静态画面直合成路径；按需导入音轨、调整字幕及输出选项。
5. 开始渲染并在项目目录中查看导出视频。

界面会显示当前阶段和可用操作。API 生成可能产生费用；手绘动画及高分辨率视频渲染也可能消耗较多时间和本机资源，请先检查所选服务、画面数量、分辨率和输出设置。

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

### 常见问题

#### 没有现成脚本，可以直接使用吗？

可以从已有 SRT 字幕开始。小憩会先根据字幕时间戳整理场景；配置文本 API 后，还可以请求模型辅助理解语义和拆分场景。字幕仍是时间轴依据，生成结果应在继续制作前检查。

#### 一定要配置 AI API 吗？

不需要才能启动应用、创建项目或浏览界面。需要调用文本理解或图像生成时，才需要配置相应服务和密钥。文本与图像 API 可以分别配置。

#### 手绘动画与静态画面直合成该选哪个？

想要逐步落笔的白板效果时选手绘动画；希望更快预览，或已有静态分镜图时选静态画面直合成。静态模式会按 SRT 时间戳排布场景并保留幕间空档。

#### 字幕、音频和 API 密钥会上传吗？

应用本身在本机运行。使用外部 AI 服务时，发送给服务的内容取决于你执行的操作，例如字幕文本、提示词或参考图；API 密钥也会用于向对应服务鉴权。未调用外部 API 时，项目数据默认保存在本机。请根据服务商政策和你对素材的授权情况决定是否提交内容。

#### 可以商用吗？

本项目自有代码按 MIT License 发布。商用时也必须分别核对内置字体、画风库、生成内容、AI 服务和 FFmpeg 的相关许可或条款；MIT 不会替代第三方素材的许可证。

#### 输出视频在哪里？

项目和生成内容默认位于仓库根目录的 `projects/` 下，每个项目一个目录。可在设置中更改项目根目录。

### 开发与检查

```bash
python -m py_compile server.py
```

运行服务：

```bash
python server.py
```

目前仓库未附带独立自动化测试套件。改动服务端后至少运行语法检查，并在浏览器中检查首页及相关工作流页面。

### 第三方资源与致谢

小憩自有代码按 [MIT License](LICENSE) 发布。项目基于 [stickman-video-director](https://github.com/kaomei/stickman-video-director) 的视频导演工作流继续优化，感谢上游项目提供的思路与基础。

项目内置并使用以下两个开源资源库：

- **[乔木封面字库（qiaomu-cover-fonts）](https://github.com/joeseesun/qiaomu-cover-fonts)**：提供本地字体文件，用于字幕预览、排版和 FFmpeg 字幕合成。字体按各自文件附带的许可分发；字库中的字体采用 SIL Open Font License 1.1，详情见 `static/qiaomu-cover-fonts/licenses/`。
- **[handraw-style 手绘画风库](https://github.com/yang0/handraw-style)**：提供画风编号、特征说明和参考预览图，用于选择视觉风格和生成分镜提示词。其 MIT 许可包含署名要求；再分发或基于该库二次开发时，请保留作者 `yang0` 和原仓库链接。

请随这些资源保留原始许可证与署名信息。各素材来源及使用方式见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和素材目录内的许可文件。FFmpeg 不随项目分发，需单独安装并遵守其适用许可。

### 参与贡献

欢迎通过 GitHub Issues 报告问题或提出功能建议，也欢迎提交 Pull Request。提交前请确认：

- 没有加入 API Key、个人字幕、音频、参考图片或生成视频。
- 新增第三方资源附带了来源和许可说明。
- 服务端通过 `python -m py_compile server.py`。

## English

### Contents

- [Overview and origins](#overview-and-origins)
- [Features](#features)
- [Production modes](#production-modes)
- [Getting started](#getting-started)
- [Installation and launch](#installation-and-launch)
- [First-time setup](#first-time-setup)
- [FAQ](#faq)
- [Data and privacy](#data-and-privacy)
- [Third-party assets and acknowledgements](#third-party-assets-and-acknowledgements)

### Overview and origins

Xiaoqi (小憩) is designed for educational videos, tutorials, podcasts, and short-form creators. Subtitles are treated as the source of truth for the video timeline: SRT timestamps are preserved while captions are organized into editable scenes, then combined with visuals and audio. The app consists of a local Python server and a static web interface; it requires no hosted account or database.

This project localizes and extends the video-director workflow established by [kaomei/stickman-video-director](https://github.com/kaomei/stickman-video-director). Xiaoqi focuses on SRT-driven production and adds subtitle timestamp validation, scene splitting and editing, storyboard review, two rendering paths (hand-drawn animation and direct still-image composition), audio alignment, and reusable character/prop/scene assets. Refer to the upstream repository for its README and license. Xiaoqi maintains its own implementation and license notices.

### Features

- **SRT-based scene workflow**: Import SRT files, inspect subtitle timing and text, and organize captions into scenes. An optional text model can help interpret content and split scenes.
- **Reviewable storyboards**: Inspect and edit scenes, prompts, and images before continuing.
- **Two production paths**: Create scene-by-scene hand-drawn animation or compose still images into a video using the original SRT timeline.
- **Configurable generation services**: Configure separate OpenAI-compatible text and image APIs, test connections, and select models.
- **Reusable visual assets**: Organize characters, props, scenes, and custom references. The repository includes the handraw-style catalog and local font assets.
- **Subtitle and audio controls**: Adjust subtitle style, placement, and font; import audio and align it to the subtitle timeline.
- **Local-first privacy**: The interface binds to `127.0.0.1` by default. API keys and project files stay on your machine and are excluded from Git.

### Production modes

| Mode | Best for | How it works | Key traits |
| --- | --- | --- | --- |
| Hand-drawn animation | A whiteboard look with drawings appearing over time | Renders scenes from reviewed storyboards, then combines them with subtitles and audio | Review or redo scenes individually; longer render times and animation dependencies may apply |
| Direct still-image composition | Fast previews, lightweight production, or finished static boards | Places each still image on the timeline using the SRT start and end times | Preserves gaps between scenes; skips per-scene animation |

Both modes use the same subtitle and scene data. Choose a production path after reviewing the storyboards.

### Getting started

1. Start the app, create a project, and import an `.srt` file from the project page.
2. Review subtitles and scene-splitting options. Configure a text API for optional semantic scene splitting.
3. Review the resulting scenes, generate or edit storyboard images, and approve each scene.
4. Choose hand-drawn animation or still-image composition. Import audio and adjust subtitle and output options as needed.
5. Render the video and find the exported file in the project directory.

The interface shows the current stage and available actions. API generation may incur charges. Hand-drawn animation and high-resolution rendering may also take significant time and local compute resources; review the provider, scene count, resolution, and output settings before starting.

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

### FAQ

#### Can I start without a finished script?

Yes. Start with an SRT file. Xiaoqi organizes scenes from subtitle timestamps; with a text API configured, a model can also help interpret the content and suggest scene splits. Review the result before continuing. SRT remains the timeline source.

#### Do I need to configure AI APIs?

No API is needed to start the app, create projects, or browse the interface. Configure a service and key only for text understanding or image generation. Text and image APIs can be configured separately.

#### Which production mode should I choose?

Choose hand-drawn animation for a progressive whiteboard drawing effect. Choose direct still-image composition for faster previews or when storyboard images are already available. The still-image path places scenes according to SRT timestamps and preserves gaps between them.

#### Are subtitles, audio, or API keys uploaded?

The app itself runs locally. When you use an external AI service, the content sent depends on the action, such as subtitle text, prompts, or reference images; API keys are also used to authenticate with their configured providers. Project data stays local by default when no external API is called. Review provider policies and your rights to the source material before sending content.

#### Can I use the project commercially?

Original project code is under the MIT License. Commercial use still requires checking the licenses or terms for bundled fonts, the style library, generated content, AI services, and FFmpeg. The MIT License does not replace third-party licenses.

#### Where are rendered videos saved?

Projects and generated files are stored under `projects/` by default, with one directory per project. The project root can be changed in settings.

### Development and checks

```bash
python -m py_compile server.py
```

Start the app with:

```bash
python server.py
```

The repository does not currently include a standalone automated test suite. After server-side changes, run the syntax check and verify the home page and affected workflow screens in a browser.

### Third-party assets and acknowledgements

Original Xiaoqi code is released under the [MIT License](LICENSE). The project extends the video-director workflow from [stickman-video-director](https://github.com/kaomei/stickman-video-director); thanks to the upstream project for its ideas and foundation.

The project bundles and uses these open-source asset libraries:

- **[Qiaomu Cover Fonts (qiaomu-cover-fonts)](https://github.com/joeseesun/qiaomu-cover-fonts)** provides local fonts for subtitle previews, layout, and FFmpeg subtitle rendering. Fonts are distributed under their accompanying licenses; fonts in the library use the SIL Open Font License 1.1. See `static/qiaomu-cover-fonts/licenses/`.
- **[handraw-style](https://github.com/yang0/handraw-style)** provides style IDs, descriptions, and reference images for choosing a visual style and composing storyboard prompts. Its MIT license includes an attribution requirement. When redistributing or building on the library, retain credit to `yang0` and the original repository link.

Keep the original license and attribution files with these assets. Sources and usage are also documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and in each asset directory. FFmpeg is not bundled and must be installed separately under its applicable license.

### Contributing

Bug reports, feature requests, and pull requests are welcome through GitHub Issues. Before submitting a change, make sure that:

- It contains no API keys, personal subtitles, audio, reference images, or rendered videos.
- New third-party assets include source and license information.
- The server passes `python -m py_compile server.py`.
