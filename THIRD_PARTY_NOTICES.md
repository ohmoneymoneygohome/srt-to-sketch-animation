# 第三方资源与许可声明

本工具将以下资源作为本地可复用资产随项目分发。资源仅用于画面预览、字幕排版和视频合成，不通过运行时从外部地址下载。

## 乔木封面字库

- 项目：`joeseesun/qiaomu-cover-fonts`
- 来源：https://github.com/joeseesun/qiaomu-cover-fonts
- 本地目录：`static/qiaomu-cover-fonts/`
- 许可：字体文件按 SIL Open Font License 1.1 分发；每款字体的完整许可文本位于 `static/qiaomu-cover-fonts/licenses/`。
- 使用方式：浏览器通过 `static/qiaomu-fonts.css` 加载；FFmpeg 字幕滤镜通过 `fontsdir` 使用同一套字体文件。

## handraw-style 画风库

- 项目：`yang0/handraw-style`
- 来源：https://github.com/yang0/handraw-style
- 本地目录：`static/handraw-style/`
- 许可：以该项目随附的 `static/handraw-style/LICENSE` 为准，并保留作者与原仓库署名要求。
- 使用方式：设置页读取 `styles.json` 的分类、编号、参考来源和特征说明，并使用 `images/individual/` 中的本地预览图；分镜提示词会保留所选编号和注释。

## 变更说明

本工具的自定义提示词、用户生成资产和项目数据属于本项目内容，不会覆盖或修改上述第三方原始许可文件。更新第三方资源时，应同步更新本声明及对应的许可文本。
