# 小憩：SRT 白板动画工作流

小憩是一款本地运行的字幕分镜与视频制作工具。它可以导入 SRT 字幕、整理分镜、配置画面生成服务，并使用 FFmpeg 将画面、字幕和音频合成为视频。

## 功能

- 导入并校验 SRT 字幕，按字幕时间轴拆分场景。
- 编辑分镜并管理可复用的角色、道具和场景素材。
- 接入 OpenAI 兼容的文本与图像生成 API；密钥保存在本机配置文件中。
- 通过手绘动画或静态画面合成流程制作视频。
- 以 `127.0.0.1` 提供本地 Web 界面，不默认暴露到局域网。

## 环境要求

- Python 3.10 或更高版本
- Pillow（图像处理）
- FFmpeg 和 FFprobe（视频导出）
- 使用 AI 生成功能时，需要兼容的 API 服务和自行提供的密钥

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python server.py
```

浏览器打开 <http://127.0.0.1:8766>。如需更换端口，可设置 `WORKFLOW_PORT` 环境变量。

首次运行后，在设置页填写文本和图像 API。配置保存在根目录的 `config.local.json`，该文件已被 Git 忽略。不要提交 API 密钥，也不要把本机生成的视频、字幕或项目文件加入公开仓库。

## 第三方素材

`static/handraw-style/` 和 `static/qiaomu-cover-fonts/` 包含第三方素材及各自的许可文件。发布或再分发时，请一并保留这些目录中的许可与署名信息，详情见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

FFmpeg/FFprobe 不随本项目分发；请从可信来源安装，并遵守其适用许可。

## 项目数据

本地工作项目默认保存在 `projects/`。该目录及本地配置、生成媒体均已加入 `.gitignore`，不会作为应用源码提交。

## 开发

服务端只使用 Python 标准库和 Pillow。语法检查：

```powershell
python -m py_compile server.py
```

## 许可证

本项目自有代码按 [MIT License](LICENSE) 分发。第三方字体、画风库和其他素材仍按其各自目录中的许可证分发，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
