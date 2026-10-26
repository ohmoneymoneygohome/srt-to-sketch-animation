import json
import os
import shutil
import subprocess
import sys
import base64
import re
import wave
import traceback
import threading
import uuid
import time
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CONFIG_PATH = ROOT / "config.local.json"
DEFAULT_PROJECT_ROOT = ROOT / "projects"

_BOARD_JOBS = {}
_BOARD_JOBS_LOCK = threading.Lock()
_ANIMATION_JOBS = {}
_ANIMATION_JOBS_LOCK = threading.Lock()


TIME_RE = re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})")


def parse_timecode(value):
    match = TIME_RE.search(str(value or ""))
    if not match:
        raise ValueError(f"无法解析时间码: {value}")
    ms = int(match.group("ms").ljust(3, "0")[:3])
    return ((int(match.group("h")) * 60 + int(match.group("m"))) * 60 + int(match.group("s"))) * 1000 + ms


def format_timecode(milliseconds):
    total = max(0, int(milliseconds))
    hours, remainder = divmod(total, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def parse_srt_text(text):
    normalized = str(text or "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", normalized.strip())
    cues = []
    for block_index, block in enumerate(blocks, 1):
        lines = [line.strip("\ufeff") for line in block.split("\n")]
        if not lines:
            continue
        time_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        timing = lines[time_index]
        parts = timing.split("-->", 1)
        try:
            start_ms = parse_timecode(parts[0])
            end_ms = parse_timecode(parts[1])
        except ValueError:
            continue
        text_lines = [line.strip() for line in lines[time_index + 1:] if line.strip()]
        if end_ms < start_ms:
            continue
        cues.append({
            "index": len(cues) + 1,
            "source_index": lines[0] if time_index > 0 else str(block_index),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start": format_timecode(start_ms),
            "end": format_timecode(end_ms),
            "text": "\n".join(text_lines),
        })
    return cues


def validate_cues(cues, validation_text=None):
    errors = []
    warnings = []
    for previous, current in zip(cues, cues[1:]):
        if current["start_ms"] < previous["end_ms"]:
            errors.append({
                "type": "overlap",
                "previous": previous["index"],
                "current": current["index"],
                "message": f"第 {previous['index']} 条与第 {current['index']} 条字幕时间重叠",
            })
        if current["start_ms"] > previous["end_ms"]:
            warnings.append({
                "type": "gap",
                "previous": previous["index"],
                "current": current["index"],
                "duration_ms": current["start_ms"] - previous["end_ms"],
                "message": f"第 {previous['index']} 条与第 {current['index']} 条之间有 {current['start_ms'] - previous['end_ms']}ms 空档（保留自然停顿）",
            })
    validation = parse_srt_text(validation_text) if validation_text else []
    mismatches = []
    if validation:
        if len(validation) != len(cues):
            mismatches.append({"type": "count", "message": f"校验稿 {len(validation)} 条，SRT {len(cues)} 条"})
        for index, (actual, expected) in enumerate(zip(cues, validation), 1):
            if actual["start_ms"] != expected["start_ms"] or actual["end_ms"] != expected["end_ms"]:
                mismatches.append({
                    "type": "time",
                    "index": index,
                    "message": f"第 {index} 条时间戳偏差：SRT {actual['start']}–{actual['end']}，校验稿 {expected['start']}–{expected['end']}",
                })
            if actual["text"].strip() != expected["text"].strip():
                mismatches.append({
                    "type": "text",
                    "index": index,
                    "message": f"第 {index} 条文字与校验稿不一致",
                    "srt": actual["text"],
                    "validation": expected["text"],
                })
    return {
        "cue_count": len(cues),
        "errors": errors,
        "warnings": warnings,
        "validation_provided": bool(validation_text),
        "validation_count": len(validation),
        "mismatches": mismatches,
        "valid": not errors,
    }


def build_scenes(cues, max_lines=3, over_limit=10, title_recognition=False, semantic_plan=None):
    try:
        max_lines = min(20, max(1, int(max_lines)))
    except (TypeError, ValueError):
        max_lines = 3
    try:
        over_limit = min(100, max(1, int(over_limit)))
    except (TypeError, ValueError):
        over_limit = 10
    scenes = []
    current = []
    over_count = 0

    def is_title_candidate(cue):
        text = str(cue.get("text") or "").strip()
        return bool(title_recognition and (
            text.endswith(("：", ":"))
            or re.match(r"^(今天|我们来聊|接下来|第一部分|第二部分|第三部分|最后|关于)", text)
        ))

    def flush():
        nonlocal current, over_count
        if not current:
            return
        scene_id = len(scenes) + 1
        source_text = current[0]["text"].strip() if current else ""
        # Title recognition is opt-in. When enabled, retain the source cue's
        # exact time range but expose a separate display title so a spoken
        # sentence can be replaced by a concise chapter heading later.
        title = bool(title_recognition and len(current) == 1 and is_title_candidate(current[0]))
        title_text = source_text
        if title:
            title_text = re.sub(r"^[，。、“”‘’：:、\s]+|[，。！？；：:、\s]+$", "", source_text)
            if len(title_text) > 18:
                title_text = title_text[:18].rstrip("，。！？；：:、") + "…"
        for cue in current:
            cue["scene"] = scene_id
        scenes.append({
            "id": scene_id,
            "title": title,
            "title_text": title_text if title else "",
            "title_source_text": source_text if title else "",
            "start_ms": current[0]["start_ms"],
            "end_ms": current[-1]["end_ms"],
            "start": current[0]["start"],
            "end": current[-1]["end"],
            "duration_ms": current[-1]["end_ms"] - current[0]["start_ms"],
            "cues": list(current),
            "status": "pending",
            "image_status": "pending",
            "animation_status": "pending",
        })
        if len(current) > max_lines:
            over_count += 1
        current = []

    # A semantic plan is produced by the text model.  It is deliberately
    # validated here before touching the timeline: every cue must occur once,
    # in source order, and no group may exceed the configured exception quota.
    if semantic_plan is not None:
        groups = semantic_plan.get("groups") if isinstance(semantic_plan, dict) else semantic_plan
        if not isinstance(groups, list) or not groups:
            return [], {"ok": False, "code": "TEXT_PLAN_EMPTY", "message": "文本理解 API 没有返回任何分组，已停止拆幕"}
        if isinstance(groups, list):
            normalized_groups = []
            expected = 1
            for raw_group in groups:
                indices = raw_group.get("cue_indices") if isinstance(raw_group, dict) else raw_group
                if not isinstance(indices, list) or not indices:
                    continue
                try:
                    indices = [int(item) for item in indices]
                except (TypeError, ValueError):
                    return [], {"ok": False, "code": "TEXT_PLAN_INVALID", "message": "文本理解 API 返回了无法解析的字幕分组索引"}
                if indices != list(range(expected, expected + len(indices))):
                    return [], {"ok": False, "code": "TEXT_PLAN_NONCONTIGUOUS", "message": f"文本理解 API 返回的第 {len(normalized_groups) + 1} 幕不是连续字幕，无法安全映射时间轴"}
                if indices[-1] > len(cues):
                    return [], {"ok": False, "code": "TEXT_PLAN_OUT_OF_RANGE", "message": "文本理解 API 返回了超出 SRT 条目范围的分组索引"}
                expected = indices[-1] + 1
                normalized_groups.append((indices, raw_group if isinstance(raw_group, dict) else {}))
            if expected != len(cues) + 1:
                return [], {"ok": False, "code": "TEXT_PLAN_INCOMPLETE", "message": f"文本理解 API 没有覆盖全部字幕（已覆盖 {expected - 1}/{len(cues)} 条）"}
            for indices, meta in normalized_groups:
                current = [cues[index - 1] for index in indices]
                scene_id = len(scenes) + 1
                source_text = current[0]["text"].strip()
                title = bool(title_recognition and meta.get("title_candidate"))
                title_text = str(meta.get("title_text") or source_text).strip() if title else ""
                if title and not title_text:
                    title_text = source_text
                for cue in current:
                    cue["scene"] = scene_id
                scenes.append({
                    "id": scene_id,
                    "title": title,
                    "title_text": title_text,
                    "title_source_text": source_text if title else "",
                    "split_reason": str(meta.get("reason") or "文本理解模型按语义连续性分组"),
                    "start_ms": current[0]["start_ms"],
                    "end_ms": current[-1]["end_ms"],
                    "start": current[0]["start"],
                    "end": current[-1]["end"],
                    "duration_ms": current[-1]["end_ms"] - current[0]["start_ms"],
                    "cues": list(current),
                    "status": "pending",
                    "image_status": "pending",
                    "animation_status": "pending",
                })
                if len(current) > max_lines:
                    over_count += 1
            if over_count > over_limit:
                return scenes, {
                    "ok": False,
                    "code": "OVER_LIMIT_EXCEEDED",
                    "message": f"语义拆分需要 {over_count} 个超限分镜，但当前只允许 {over_limit} 个。请提高“允许超限幕数”或调整字幕。",
                    "over_limit_count": over_count,
                }
            return scenes, {"ok": True, "over_limit_count": over_count, "source": "text_api"}

    for cue in cues:
        # When enabled, a likely chapter cue gets its own original time slice.
        # This prevents a title line from being merged into the following
        # spoken content and does not add any duration.
        if is_title_candidate(cue) and current:
            flush()
        current.append(cue)
        if is_title_candidate(cue):
            flush()
            continue
        # Keep a short subtitle block together; split only at the configured soft limit.
        if len(current) >= max_lines:
            flush()
    flush()
    if over_count > over_limit:
        return scenes, {
            "ok": False,
            "code": "OVER_LIMIT_EXCEEDED",
            "message": f"需要超过上限的分镜有 {over_count} 个，已超过允许的 {over_limit} 个",
            "over_limit_count": over_count,
        }
    return scenes, {"ok": True, "over_limit_count": over_count, "source": "local_fallback"}


def project_state_path(folder):
    return folder / "workflow.json"


def read_project_state(folder):
    path = project_state_path(folder)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_project_state(folder, state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    project_state_path(folder).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def decode_upload(value):
    if not isinstance(value, dict):
        return None, None
    name = Path(str(value.get("name") or "素材")).name
    encoded = value.get("data")
    if not isinstance(encoded, str):
        return name, None
    if "," in encoded and encoded.lstrip().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        return name, base64.b64decode(encoded)
    except (ValueError, base64.binascii.Error):
        return name, None


def ffprobe_duration(path):
    status = ffmpeg_status()
    probe = status["ffprobe"]["path"]
    if not probe or not Path(path).is_file():
        return None
    try:
        result = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30, check=True
        )
        return float(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def safe_export_name(value):
    name = Path(str(value or "成片.mp4")).name
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:120] or "成片.mp4"


def ensure_placeholder_board(folder, scene, width=960, height=540):
    """Create a deterministic paper-color still for local pipeline tests."""
    boards = folder / "boards"
    boards.mkdir(parents=True, exist_ok=True)
    target = boards / f"scene_{int(scene.get('id', 0)):03d}.ppm"
    if not target.is_file():
        # A warm paper field is deliberately simple: real image-generation
        # providers can replace this file later without changing the renderer.
        pixel = bytes((244, 232, 207))
        target.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixel * (width * height))
    return target


def prepare_srt_aligned_audio(folder, source_audio, origin_ms, finish_ms, ffmpeg=None):
    """Keep audio only inside SRT cue windows and place each window at its SRT time."""
    folder = Path(folder).resolve()
    source_audio = Path(source_audio).resolve()
    if folder not in source_audio.parents or not source_audio.is_file():
        raise ValueError("项目音轨文件不存在或路径不在当前项目内")
    state = read_project_state(folder)
    srt_info = state.get("inputs", {}).get("srt", {}) or {}
    srt_path = Path(str(srt_info.get("path") or "")).resolve()
    if folder not in srt_path.parents or not srt_path.is_file():
        raise ValueError("项目中找不到最初导入的 SRT，无法按字幕时间码裁切音轨")
    cues = parse_srt_text(srt_path.read_text(encoding="utf-8-sig", errors="replace"))
    if not cues:
        raise ValueError("原始 SRT 中没有有效时间段，无法对齐音轨")

    intervals = []
    for cue in cues:
        start = max(int(origin_ms), int(cue["start_ms"]))
        end = min(int(finish_ms), int(cue["end_ms"]))
        if end > start:
            if intervals and start <= intervals[-1][1]:
                intervals[-1][1] = max(intervals[-1][1], end)
            else:
                intervals.append([start, end])
    if not intervals:
        raise ValueError("SRT 字幕时间段与成片时间范围没有交集")

    source_duration = ffprobe_duration(source_audio)
    if source_duration is None:
        raise ValueError("无法读取音轨时长，不能安全按 SRT 裁切")
    source_duration_ms = round(source_duration * 1000)
    out_of_range = next((end for _, end in intervals if end > source_duration_ms), None)
    if out_of_range is not None:
        raise ValueError(
            f"SRT 时间超出音轨长度：需要音频到 {format_timecode(out_of_range)}，"
            f"但音轨只有 {format_timecode(source_duration_ms)}"
        )

    duration_ms = int(finish_ms) - int(origin_ms)
    if duration_ms <= 0:
        raise ValueError("成片时间范围无效，无法对齐音轨")
    source_stat = source_audio.stat()
    srt_stat = srt_path.stat()
    cache_identity = "|".join(map(str, (
        source_audio, source_stat.st_size, source_stat.st_mtime_ns,
        srt_path, srt_stat.st_size, srt_stat.st_mtime_ns, origin_ms, finish_ms,
    )))
    cache_key = hashlib.sha256(cache_identity.encode("utf-8")).hexdigest()[:16]
    output_dir = folder / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    aligned_audio = output_dir / f"audio_srt_aligned_{cache_key}.m4a"
    if aligned_audio.is_file() and aligned_audio.stat().st_size > 0:
        return aligned_audio, {"cue_segments": len(intervals), "cached": True, "duration_ms": duration_ms}

    ffmpeg = ffmpeg or ffmpeg_status()["ffmpeg"]["path"]
    if not ffmpeg:
        raise ValueError("未找到 FFmpeg，无法按 SRT 时间码处理音轨")
    filters = []
    labels = []
    for index, (start, end) in enumerate(intervals):
        delay_ms = max(0, start - int(origin_ms))
        label = f"a{index}"
        filters.append(
            f"[0:a:0]atrim=start={start / 1000:.3f}:end={end / 1000:.3f},"
            f"asetpts=PTS-STARTPTS,adelay=delays={delay_ms}:all=1[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,"
        f"apad,atrim=duration={duration_ms / 1000:.3f}[aligned]"
    )
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_audio),
        "-filter_complex", ";".join(filters), "-map", "[aligned]", "-t", f"{duration_ms / 1000:.3f}",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(aligned_audio),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode or not aligned_audio.is_file() or aligned_audio.stat().st_size == 0:
        detail = (result.stderr or "FFmpeg 没有生成对齐音轨")[-2000:]
        raise ValueError(f"按 SRT 时间码裁切音轨失败：{detail}")
    return aligned_audio, {"cue_segments": len(intervals), "cached": False, "duration_ms": duration_ms}


def project_audio_path(folder, payload=None):
    payload = payload or {}
    state_audio = read_project_state(folder).get("inputs", {}).get("audio", {}) or {}
    candidate = Path(str(payload.get("audio_path") or state_audio.get("path") or "")).resolve()
    folder = Path(folder).resolve()
    return candidate if folder in candidate.parents and candidate.is_file() else None


def extend_scene_windows_to_audio(folder, scenes, payload=None):
    """Use next scene start, and the complete audio end for the final scene."""
    ordered = sorted((dict(item or {}) for item in scenes), key=lambda item: int(item.get("start_ms", 0)))
    audio_path = project_audio_path(folder, payload)
    audio_duration_ms = None
    if audio_path:
        duration = ffprobe_duration(audio_path)
        if duration is not None:
            audio_duration_ms = round(duration * 1000)
    for index, scene in enumerate(ordered):
        start = int(scene.get("start_ms", 0))
        if index + 1 < len(ordered):
            end = int(ordered[index + 1].get("start_ms", scene.get("end_ms", start + 1)))
        else:
            end = audio_duration_ms if audio_duration_ms is not None else int(scene.get("end_ms", start + 1))
        scene["end_ms"] = max(start + 1, end)
        scene["duration_ms"] = scene["end_ms"] - start
        scene["end"] = format_timecode(scene["end_ms"])
    return ordered, audio_path, audio_duration_ms


def render_static_video(folder, payload):
    """Render one still per scene, holding each still until the next SRT scene."""
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return {"ok": False, "message": "没有可合成的分镜"}
    ffmpeg = ffmpeg_status()["ffmpeg"]["path"]
    if not ffmpeg:
        return {"ok": False, "message": "未找到 FFmpeg"}

    try:
        fps = max(1, min(60, int(payload.get("fps", 30))))
        width, height = {
            "16:9": (1920, 1080),
            "9:16": (1080, 1920),
            "1:1": (1080, 1080),
        }.get(payload.get("ratio"), (1920, 1080))
        normalized = []
        effective_scenes, effective_audio_path, audio_duration_ms = extend_scene_windows_to_audio(folder, scenes, payload)
        for scene in effective_scenes:
            start = int(scene["start_ms"])
            end = int(scene["end_ms"])
            if end <= start:
                raise ValueError(f"分镜 {scene.get('id')} 的时间范围无效")
            rel = Path(str(scene.get("image_path") or ""))
            image = (folder / rel).resolve()
            if folder.resolve() not in image.parents or not image.is_file():
                # Keep the branch testable before an image provider is wired:
                # explicit image paths are preferred, otherwise create a
                # clearly marked local placeholder still.
                image = ensure_placeholder_board(folder, scene)
            normalized.append((start, end, image, scene))
        normalized.sort(key=lambda item: item[0])
        if any(cur[0] < prev[1] for prev, cur in zip(normalized, normalized[1:])):
            raise ValueError("分镜时间范围互相重叠，请先校正拆幕")

        origin = normalized[0][0]
        finish = max(item[1] for item in normalized)
        if audio_duration_ms is not None:
            finish = max(finish, audio_duration_ms)
        total_ms = finish - origin
        if total_ms <= 0:
            raise ValueError("无法确定成片时长")

        out_dir = folder / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / safe_export_name(payload.get("filename"))
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        durations = []
        for index, (start, end, image, _scene) in enumerate(normalized):
            next_start = normalized[index + 1][0] if index + 1 < len(normalized) else finish
            # Hold the current still over any natural SRT silence before the next scene.
            span = max(1, next_start - start)
            durations.append(span / 1000)
            command.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{span / 1000:.3f}", "-i", str(image)])

        audio_path = effective_audio_path
        audio_alignment = {"mode": "srt_forward_fill", "audio_duration_ms": audio_duration_ms} if audio_path else None
        if audio_path:
            command.extend(["-i", str(audio_path)])
        subtitle_path = None
        if payload.get("include_subtitles", True):
            subtitle_path = prepare_merged_subtitles(folder, origin, finish)
            if not subtitle_path:
                raise ValueError("已开启字幕烧录，但项目中找不到可用的最初导入 SRT。请返回项目设置确认 SRT 已导入，或关闭字幕烧录选项。")
            command.extend(["-i", str(subtitle_path)])

        filters = []
        labels = []
        for index in range(len(normalized)):
            label = f"v{index}"
            filters.append(
                f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
                f"format=yuv420p,setpts=PTS-STARTPTS[{label}]"
            )
            labels.append(f"[{label}]")
        filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[basev]")
        if subtitle_path:
            filters.append(f"[basev]{subtitle_filter(subtitle_path, payload.get('subtitle_settings'))}[outv]")
        else:
            filters.append("[basev]null[outv]")
        command.extend([
            "-filter_complex", ";".join(filters), "-map", "[outv]",
            "-t", f"{total_ms / 1000:.3f}", "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        ])

        if audio_path:
            command.extend(["-map", f"{len(normalized)}:a:0?", "-c:a", "aac", "-b:a", "192k"])
        if subtitle_path:
            subtitle_index = len(normalized) + (1 if audio_path else 0)
            # Keep an optional selectable subtitle track as well. The visible
            # burn-in is already applied to [outv] above.
            command.extend(["-map", f"{subtitle_index}:0", "-c:s", "mov_text"])

        command.extend(["-movflags", "+faststart", str(output)])
        result = subprocess.run(command, capture_output=True, text=True, timeout=900)
        if result.returncode:
            return {"ok": False, "message": (result.stderr or "FFmpeg 合成失败")[-2000:]}

        probe = ffprobe_duration(output)
        state = read_project_state(folder)
        state["static_render"] = {
            "output": str(output),
            "duration_ms": round(probe * 1000) if probe is not None else None,
            "timeline_duration_ms": total_ms,
            "fps": fps,
            "ratio": payload.get("ratio", "16:9"),
            "scene_count": len(normalized),
            "mode": "static",
            "audio_included": bool(audio_path),
            "audio_alignment": audio_alignment,
            "subtitles_included": bool(subtitle_path),
            "subtitles_burned_in": bool(subtitle_path),
            "segments": [
                {
                    "scene_id": item[3].get("id"),
                    "start_ms": item[0],
                    "end_ms": item[1],
                    "image": str(item[2]),
                }
                for item in normalized
            ],
        }
        state["render_mode"] = "静态画面直合成"
        state["stage"] = "export"
        write_project_state(folder, state)
        return {
            "ok": True, "output": str(output), "duration_ms": state["static_render"]["duration_ms"],
            "timeline_duration_ms": total_ms, "scene_count": len(normalized), "fps": fps,
            "audio_alignment": audio_alignment,
            "subtitles_included": bool(subtitle_path),
            "subtitles_burned_in": bool(subtitle_path),
            "output_relative": str(output.relative_to(folder)),
            "output_url": scene_image_url(payload.get("project_id"), output.relative_to(folder)),
        }
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": str(exc)}


def whiteboard_renderer_script():
    """Locate the optional stream renderer without bundling its dependencies."""
    candidates = [
        Path.home() / ".codex" / "skills" / "srt-whiteboard-animation" / "scripts" / "render_stream_whiteboard.py",
        ROOT.parent / "scripts" / "render_stream_whiteboard.py",
    ]
    return next((path for path in candidates if path.is_file()), None)


def whiteboard_renderer_python():
    """Use the skill's isolated runtime so OpenCV/AV do not depend on server Python."""
    skill_root = Path.home() / ".codex" / "skills" / "srt-whiteboard-animation"
    candidates = [
        skill_root / ".venv" / "Scripts" / "python.exe",
        skill_root / ".venv" / "bin" / "python",
    ]
    return next((path for path in candidates if path.is_file()), Path(sys.executable)
    )


def build_full_frame_annotation(image_path, scene, settings):
    """Create a no-mask, full-canvas annotation for continuous drawing.

    The workflow deliberately uses one full-frame element rather than semantic
    rectangular masks. This keeps the pen moving over the complete illustration
    and avoids the regular blank blocks that appeared in the earlier mask mode.
    """
    from PIL import Image

    with Image.open(image_path) as image:
        width, height = image.size
    start_ms = int(scene.get("start_ms", 0))
    end_ms = int(scene.get("end_ms", start_ms + 1000))
    duration_ms = max(1000, end_ms - start_ms)
    label = str(scene.get("title_text") or "全画面连续绘制")
    return {
        "sceneId": str(scene.get("id") or ""),
        "canvas": {"width": width, "height": height},
        "storyBasis": "按 SRT 时间轴进行全画面连续绘制，不使用区域遮罩",
        "sceneDurationMs": duration_ms,
        "elements": [{
            "id": "full-frame",
            "label": label,
            "sequence": 1,
            "narrativeRole": "整幅画面连续落墨",
            "subtitle": "\n".join(str(c.get("text") or "") for c in scene.get("cues", [])),
            "type": "full_frame",
            "region": {"x": 0, "y": 0, "width": width, "height": height},
            "reveal": {
                "direction": "full_frame",
                "startMs": 0,
                "durationMs": duration_ms,
                "maskPaddingPx": 0,
                "protectedRegions": [],
            },
            "handPath": {
                "start": [0, 0],
                "end": [width, height],
                "easing": "easeInOut",
            },
        }],
        "background": {
            "mode": "ffmpeg_final_layer",
            "paper": settings.get("paper", "暖米黄"),
        },
    }


def render_project_animations(folder, project_id, scenes, settings, selected_ids=None, on_progress=None):
    """Render selected scenes through the installed stream renderer.

    No automatic retry is performed. A failed scene is returned to the UI for
    explicit manual rerun, matching the product workflow.
    """
    renderer = whiteboard_renderer_script()
    if renderer is None:
        return {"ok": False, "message": "未找到 render_stream_whiteboard.py，请安装/配置白板动画技能"}
    selected = None if selected_ids is None else {str(value) for value in selected_ids}
    total = sum(
        1 for item in scenes
        if selected is None or str((item or {}).get("id") or (item or {}).get("scene_id") or "") in selected
    )
    anim_dir = folder / "animation"
    ann_dir = anim_dir / "annotations"
    anim_dir.mkdir(parents=True, exist_ok=True)
    ann_dir.mkdir(parents=True, exist_ok=True)
    results = []
    updated = []
    ordered_scenes, audio_path, audio_duration_ms = extend_scene_windows_to_audio(folder, scenes)
    for scene_index, raw in enumerate(ordered_scenes):
        scene = dict(raw or {})
        sid = str(scene.get("id") or scene.get("scene_id") or "")
        if selected is not None and sid not in selected:
            result = {"id": sid, "animation_status": scene.get("animation_status", "pending"), "skipped": True}
            results.append(result)
            updated.append(scene)
            if on_progress:
                on_progress("scene_completed", scene, result, len(results), total)
            continue
        rel = str(scene.get("image_path") or scene.get("imagePath") or "").replace("\\", "/")
        image_path = (folder / rel).resolve()
        if folder.resolve() not in image_path.parents or not image_path.is_file():
            scene["animation_status"] = "failed"
            scene["animation_error"] = "缺少已确认的分镜图片，请先生成并确认该分镜"
            result = {"id": sid, "animation_status": "failed", "error": scene["animation_error"], "render_duration_sec": 0}
            results.append(result)
            updated.append(scene)
            if on_progress:
                on_progress("scene_completed", scene, result, len(results), total)
            continue
        scene["animation_status"] = "rendering"
        scene["animation_error"] = ""
        scene_start_time = time.monotonic()
        if on_progress:
            on_progress("scene_started", scene, None, len(results), total)
        try:
            # The scene window already includes the gap until the next scene,
            # or the complete audio end for the final scene.
            annotation = build_full_frame_annotation(image_path, scene, settings)
            ann_path = ann_dir / f"scene_{int(scene.get('id', 0)):03d}.annotation.json"
            ann_path.write_text(json.dumps(annotation, ensure_ascii=False, indent=2), encoding="utf-8")
            output_path = anim_dir / f"scene_{int(scene.get('id', 0)):03d}.mp4"
            pen_layer = production_pen_file(settings)
            if pen_layer is None:
                pen_layer = Path.home() / ".codex" / "skills" / "srt-whiteboard-animation" / "assets" / "drawing-hand.png"
            command = [
                str(whiteboard_renderer_python()), str(renderer), str(image_path), str(ann_path), str(output_path), str(pen_layer),
                "--total-ms", str(annotation["sceneDurationMs"]),
                "--fps", str(max(1, min(60, int(settings.get("fps", 30))))),
                "--ink-path", "grid", "--color-fill", "contour-wipe",
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=1800)
            render_duration_sec = max(0.0, round(time.monotonic() - scene_start_time, 2))
            if completed.returncode:
                raise RuntimeError((completed.stderr or completed.stdout or "动画渲染失败")[-2000:])
            scene.update({
                "animation_status": "ready",
                "animation_path": str(output_path.relative_to(folder)),
                "annotation_path": str(ann_path.relative_to(folder)),
                "animation_error": "",
                "animation_render_seconds": render_duration_sec,
                "animation_generated_at": datetime.now(timezone.utc).isoformat(),
            })
            result = {
                "id": sid, "animation_status": "ready",
                "animation_path": scene["animation_path"],
                "annotation_path": scene["annotation_path"],
                "animation_url": scene_image_url(project_id, scene["animation_path"]),
                "render_duration_sec": render_duration_sec,
            }
        except Exception as exc:
            render_duration_sec = max(0.0, round(time.monotonic() - scene_start_time, 2))
            scene["animation_status"] = "failed"
            scene["animation_error"] = str(exc)
            scene["animation_render_seconds"] = render_duration_sec
            result = {"id": sid, "animation_status": "failed", "error": str(exc), "render_duration_sec": render_duration_sec}
        results.append(result)
        updated.append(scene)
        if on_progress:
            on_progress("scene_completed", scene, result, len(results), total)
    return {"ok": True, "scenes": updated, "results": results}


def render_animation_video(folder, payload):
    """Concatenate rendered single-scene MP4s and optionally add the source audio."""
    ffmpeg = ffmpeg_status()["ffmpeg"]["path"]
    if not ffmpeg:
        return {"ok": False, "message": "未找到 FFmpeg"}
    scenes = payload.get("scenes") or read_project_state(folder).get("scenes") or []
    ordered, audio_source_path, audio_duration_ms = extend_scene_windows_to_audio(folder, scenes, payload)
    if not ordered:
        return {"ok": False, "message": "没有可合并的动画分镜"}
    timeline_origin = 0 if audio_source_path else min(int(item.get("start_ms", 0)) for item in ordered)
    timeline_finish = max(int(item.get("end_ms", 0)) for item in ordered)
    timeline_duration_ms = timeline_finish - timeline_origin
    if timeline_duration_ms <= 0:
        return {"ok": False, "message": "动画分镜的 SRT 时间范围无效，无法确定成片时长"}
    clips = []
    for scene in ordered:
        rel = str(scene.get("animation_path") or "").replace("\\", "/")
        clip = (folder / rel).resolve()
        if folder.resolve() not in clip.parents or not clip.is_file():
            return {"ok": False, "message": f"分镜 {scene.get('id')} 的动画文件不存在"}
        clips.append(clip)
    out_dir = folder / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / safe_export_name(payload.get("filename") or "成片.mp4")
    concat_file = out_dir / "animation_concat.txt"
    concat_lines = []
    for path in clips:
        escaped = str(path).replace("'", "'\\''")
        concat_lines.append("file '" + escaped + "'\n")
    concat_file.write_text("".join(concat_lines), encoding="utf-8")
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file)]
    state = read_project_state(folder)
    audio_info = state.get("inputs", {}).get("audio", {})
    audio_path = audio_source_path
    audio_alignment = {"mode": "srt_forward_fill", "audio_duration_ms": audio_duration_ms} if audio_path else None
    subtitle_path = None
    if payload.get("include_subtitles", True):
        subtitle_path = prepare_merged_subtitles(folder, timeline_origin, timeline_finish)
        if not subtitle_path:
            return {"ok": False, "message": "已开启字幕烧录，但项目中找不到可用的最初导入 SRT。请返回项目设置确认 SRT 已导入，或关闭字幕烧录选项。"}
    # Add every input before output mapping/codec options. FFmpeg treats
    # -map and -c:* as output options, so placing a later -i after them breaks
    # the animation export path.
    if audio_path:
        command.extend(["-i", str(audio_path)])
    if subtitle_path:
        command.extend(["-i", str(subtitle_path)])
    command.extend(["-map", "0:v:0"])
    if audio_path:
        command.extend(["-map", "1:a:0?", "-c:a", "aac"])
    if subtitle_path:
        command.extend([
            "-vf", subtitle_filter(subtitle_path, payload.get("subtitle_settings")),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p",
        ])
    else:
        command.extend(["-c:v", "copy"])
    if subtitle_path:
        subtitle_index = 2 if audio_path else 1
        command.extend(["-map", f"{subtitle_index}:0", "-c:s", "mov_text"])
    command.extend(["-t", f"{timeline_duration_ms / 1000:.3f}", "-movflags", "+faststart", str(output)])
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if result.returncode:
        return {"ok": False, "message": (result.stderr or "动画合并失败")[-2000:]}
    duration = ffprobe_duration(output)
    state["animation_render"] = {
        "output": str(output),
        "duration_ms": round(duration * 1000) if duration is not None else None,
        "scene_count": len(clips),
        "audio_included": bool(audio_path),
        "audio_alignment": audio_alignment,
        "subtitles_included": bool(subtitle_path),
        "subtitles_burned_in": bool(subtitle_path),
        "mode": "full_frame_stream",
    }
    state["stage"] = "export"
    write_project_state(folder, state)
    return {
        "ok": True, "output": str(output),
        "duration_ms": state["animation_render"]["duration_ms"],
        "scene_count": len(clips), "subtitles_included": bool(subtitle_path),
        "audio_alignment": audio_alignment,
        "subtitles_burned_in": bool(subtitle_path),
        "output_relative": str(output.relative_to(folder)),
        "output_url": scene_image_url(payload.get("project_id"), output.relative_to(folder)),
    }


def load_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(data):
    current = load_config()
    text_api = data.get("text_api", current.get("text_api", {}))
    image_api = data.get("image_api", current.get("image_api", {}))
    for section, submitted in (("text_api", text_api), ("image_api", image_api)):
        submitted = dict(submitted or {})
        submitted_key = submitted.get("api_key", "")
        if not submitted_key and current.get(section, {}).get("api_key"):
            # Leaving the field blank means “keep the locally saved secret”.
            submitted["api_key"] = current.get(section, {}).get("api_key", "")
        elif isinstance(submitted_key, str) and submitted_key.startswith("••••••"):
            submitted["api_key"] = current.get(section, {}).get("api_key", "")
        if section == "text_api":
            text_api = submitted
        else:
            image_api = submitted
    safe = {
        "text_api": text_api,
        "image_api": image_api,
        # Environment saves must not erase business workflow settings.
        "preferences": data.get("preferences", current.get("preferences", {})),
        "project_root": data.get("project_root", current.get("project_root", str(DEFAULT_PROJECT_ROOT))),
    }
    CONFIG_PATH.write_text(
        json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def project_root():
    configured = load_config().get("project_root") or str(DEFAULT_PROJECT_ROOT)
    return Path(configured).expanduser().resolve()


def visual_asset_root():
    """Global reusable visual assets, shared by every project."""
    root = project_root() / "visual_assets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def visual_asset_index_path():
    return visual_asset_root() / "index.json"


def read_visual_assets():
    path = visual_asset_index_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_visual_assets(data):
    visual_asset_index_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def visual_asset_file(relative_path):
    raw = str(relative_path or "").replace("\\", "/")
    candidate = (visual_asset_root() / raw).resolve()
    root = visual_asset_root().resolve()
    if candidate == root or root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def production_pen_file(settings):
    """Resolve the transparent production pen layer, never the demo image."""
    asset_type = str((settings or {}).get("penAssetType") or "pen")
    value = str((settings or {}).get("penObject") or (settings or {}).get("pen") or "羽毛笔") if asset_type == "object" else str((settings or {}).get("pen") or "羽毛笔")
    assets = read_visual_assets()
    for item in assets.values():
        if item.get("key") in ({"penObject", "pen"} if asset_type == "object" else {"pen"}) and item.get("value") == value:
            candidate = visual_asset_file(item.get("production_path") or "")
            if candidate:
                return candidate
    return None


VISUAL_PRESET_CATALOG = {
    "pen": ["羽毛笔", "钢笔", "毛笔", "铅笔", "粉笔", "马克笔", "针管笔", "炭笔", "蜡笔", "圆珠笔"],
    "style": ["钢笔线稿", "水墨线稿", "素描", "水彩线稿", "版画", "极简线稿", "水墨", "漫画分镜", "建筑速写", "儿童绘本", "概念图解"],
    "paper": ["暖米黄", "宣纸", "牛皮纸", "灰白纸", "旧书页", "点阵纸", "羊皮纸", "手账纸", "黑板", "纯白卡纸"],
}

STYLE_REFERENCE_PROMPTS = {
    "钢笔线稿": (
        "生成一张横向 16:9 的完整画面参考图，主题为安静书店里一位读者站在书架旁翻阅书籍，"
        "以黑色钢笔线稿表达。用清晰、克制、略有自然粗细变化的轮廓线与细密排线塑造人物、书架和空间；"
        "明暗只由线条疏密形成，保留大片干净留白，画面结构准确、主体明确、细节适度。"
        "整体像精致但仍可手绘描摹的钢笔速写，不要铅笔擦痕、墨色晕染、水彩、照片感或复杂背景。"
        "不出现文字、字母、数字、标签、水印、UI、边框。"
    ),
    "水墨线稿": (
        "生成一张横向 16:9 的完整画面参考图，主题为安静书店里一位读者站在书架旁翻阅书籍，"
        "以东方水墨线稿表达。用有提按变化的墨线概括人物、书架和空间，墨色有浓淡、干湿与少量飞白；"
        "以疏朗留白形成空间气息，仅在必要处用极淡墨色渲染层次，主体必须清楚可辨。"
        "整体含蓄、宁静，保留可描摹的清晰结构，不要泼墨遮挡主体，不要钢笔排线、铅笔排线、鲜艳色块或复杂山水背景。"
        "不出现文字、字母、数字、标签、水印、UI、边框。"
    ),
    "素描": (
        "生成一张横向 16:9 的完整画面参考图，主题为安静书店里一位读者站在书架旁翻阅书籍，"
        "以铅笔素描表达。使用清楚的结构线、自然的轻重线条和疏密排线表现人物、书籍及空间体积；"
        "黑白灰层次适中，保留少量轻淡起稿线和大面积留白，主体结构准确、画面简洁，避免照片级写实。"
        "整体像干净的手绘铅笔速写，不要墨水晕染、钢笔交叉排线、水彩色块、擦脏背景或过度细节。"
        "不出现文字、字母、数字、标签、水印、UI、边框。"
    ),
}

PAPER_REFERENCE_PROMPTS = {
    "暖米黄": (
        "生成一张横向 16:9 的纯纸张底纹参考图，表现温和的暖米黄色手工纸。"
        "画面必须是完整铺满画布的平整纸面，色调均匀、低对比，仅有极轻微细纤维和自然纸浆颗粒，"
        "方便作为视频底层长期显示并承载黑色手绘线稿。不要卷边、折痕、污渍、破损、阴影、投影、桌面或透视角度。"
        "不出现画笔、手、图案、文字、字母、数字、水印、边框或任何其他物体。"
    ),
    "宣纸": (
        "生成一张横向 16:9 的纯纸张底纹参考图，表现干净的传统浅暖白宣纸。"
        "画面必须是完整铺满画布的平整纸面，细腻可见但非常克制的天然纸纤维与轻微纸浆纹理，"
        "底色均匀、低对比，适合黑色墨线绘制且不能干扰字幕阅读。不要出现墨迹、折痕、卷边、污渍、破损、阴影、投影、桌面或透视角度。"
        "不出现画笔、手、图案、文字、字母、数字、水印、边框或任何其他物体。"
    ),
    "牛皮纸": (
        "生成一张横向 16:9 的纯纸张底纹参考图，表现自然浅棕色牛皮纸。"
        "画面必须是完整铺满画布的平整纸面，具有细微均匀的纸纤维与纸浆颗粒，棕色温暖但亮度充足，"
        "纹理低对比，便于黑色线稿与字幕清楚显示。不要邮戳、污渍、折痕、卷边、破损、阴影、投影、桌面或透视角度。"
        "不出现画笔、手、图案、文字、字母、数字、水印、边框或任何其他物体。"
    ),
}


def visual_asset_catalog(settings=None):
    """Return all named presets and prompts without making generation calls."""
    settings = settings or {}
    result = {}
    labels = {"pen": "画笔", "style": "画面参考", "paper": "纸张"}
    for key, values in VISUAL_PRESET_CATALOG.items():
        for value in values:
            asset_id = f"{key}:{value}"
            result[asset_id] = {
                "asset_id": asset_id,
                "key": key,
                "value": value,
                "name": value,
                "kind": labels[key],
                "prompt": preset_reference_prompt(key, value, labels[key], settings),
                "status": "pending",
                "source": "preset",
            }
    return result


def safe_project_path(project_id):
    if not isinstance(project_id, str) or not project_id or project_id in (".", ".."):
        return None
    if any(part in project_id for part in ("/", "\\", ":", "\0")):
        return None
    root = project_root()
    candidate = (root / project_id).resolve()
    if candidate.parent != root or not candidate.is_dir():
        return None
    return candidate


def write_json_recoverable(path, payload):
    """Write state through a sibling temp file, with a clear lock diagnostic."""
    path = Path(path)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)
    except PermissionError as exc:
        # A reader may hold the destination open on Windows. Preserve the new state
        # under a recovery name so parsing itself is not reported as failed.
        recovery = path.with_name(f"{path.stem}.recovery{path.suffix}")
        try:
            os.replace(tmp, recovery)
            return recovery
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            # State persistence is best-effort; the API can still return the
            # parsed cues/scenes so the user can continue in this session.
            return None


def list_projects():
    root = project_root()
    if not root.is_dir():
        return {"root": str(root), "projects": []}
    projects = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink() or folder.name == "visual_assets":
            continue
        meta_path = folder / "project.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            meta = {}
        files = [path for path in folder.rglob("*") if path.is_file()]
        latest = max((path.stat().st_mtime for path in files), default=folder.stat().st_mtime)
        videos = sorted(
            [path for path in files if path.suffix.lower() == ".mp4"],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        latest_video = videos[0] if videos else None
        video_history = [
            {
                "path": str(path.relative_to(folder)).replace("\\", "/"),
                "url": scene_image_url(folder.name, path.relative_to(folder)),
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            }
            for path in videos[:12]
        ]
        projects.append({
            "id": folder.name,
            "name": meta.get("name") or folder.name,
            "stage": meta.get("stage") or "import",
            "created_at": meta.get("created_at") or datetime.fromtimestamp(folder.stat().st_ctime, timezone.utc).isoformat(),
            "updated_at": datetime.fromtimestamp(latest, timezone.utc).isoformat(),
            "subtitle_count": meta.get("subtitle_count", 0),
            "scene_count": meta.get("scene_count", 0),
            "file_count": len(files),
            "video_count": sum(path.suffix.lower() == ".mp4" for path in files),
            "image_count": sum(path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") for path in files),
            "latest_video": str(latest_video.relative_to(folder)).replace("\\", "/") if latest_video else "",
            "latest_video_url": scene_image_url(folder.name, latest_video.relative_to(folder)) if latest_video else "",
            "video_history": video_history,
        })
    projects.sort(key=lambda item: item["updated_at"], reverse=True)
    return {"root": str(root), "projects": projects}


def create_project(name):
    clean_name = " ".join(str(name or "").split()).strip()
    if not clean_name or len(clean_name) > 80:
        return None, "项目名称不能为空，且不能超过 80 个字符"
    safe_name = "".join(ch for ch in clean_name if ch.isalnum() or ch in " -_（）()").strip(" .-_")
    if not safe_name:
        safe_name = "新项目"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = project_root()
    root.mkdir(parents=True, exist_ok=True)
    folder_id = f"{safe_name[:48]}-{stamp}"
    folder = root / folder_id
    suffix = 1
    while folder.exists():
        suffix += 1
        folder = root / f"{folder_id}-{suffix}"
    try:
        folder.mkdir()
    except OSError as exc:
        if getattr(exc, "winerror", None) == 5 or getattr(exc, "errno", None) == 13:
            return None, (
                f"Windows 拒绝创建项目文件夹。\n"
                f"目标：{folder}\n"
                f"项目根目录：{root}\n"
                f"父目录存在：{root.is_dir()}\n"
                "当前目录权限检查允许写入；请检查 Windows 安全中心“受控文件夹访问”、"
                "杀毒软件拦截，或确认本地工作流服务与浏览器使用同一 Windows 用户启动。"
            )
        return None, f"创建项目文件夹失败：{type(exc).__name__}: {exc}\n目标：{folder}"
    now = datetime.now(timezone.utc).isoformat()
    meta = {"id": folder.name, "name": clean_name, "created_at": now, "updated_at": now,
            "stage": "import", "subtitle_count": 0, "scene_count": 0}
    try:
        (folder / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return None, f"项目文件夹已创建，但写入 project.json 失败：{type(exc).__name__}: {exc}\n目录：{folder}"
    return meta, None


def update_project(payload):
    folder = safe_project_path(payload.get("id"))
    if folder is None:
        return False
    meta_path = folder / "project.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {"id": folder.name, "name": folder.name}
        for field in ("stage", "subtitle_count", "scene_count"):
            if field in payload:
                meta[field] = payload[field]
        if "name" in payload and isinstance(payload["name"], str) and payload["name"].strip():
            meta["name"] = payload["name"].strip()[:80]
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except (OSError, json.JSONDecodeError):
        return False


def normalize_base_url(url):
    return (url or "").rstrip("/")


def api_test_config(payload, section):
    saved = load_config().get(section, {})
    base_url = normalize_base_url(payload.get("base_url") or saved.get("base_url"))
    api_key = str(payload.get("api_key") or "")
    if not api_key or api_key.startswith("••••••"):
        api_key = str(saved.get("api_key") or "")
    return base_url, api_key


def api_error_message(exc, api_key):
    if isinstance(exc, urllib.error.HTTPError):
        detail = exc.read(1000).decode("utf-8", errors="replace")
        detail = detail.replace(api_key, "[已隐藏 API Key]") if api_key else detail
        return f"HTTP {exc.code}: {detail or exc.reason}"
    message = str(exc)
    return message.replace(api_key, "[已隐藏 API Key]") if api_key else message


def fetch_openai_models(base_url, api_key):
    if not base_url:
        raise ValueError("缺少 Base URL")
    if not api_key:
        raise ValueError("未找到 API Key，请先填写并保存密钥")
    url = base_url if base_url.endswith("/models") else f"{base_url}/v1/models"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "srt-whiteboard-workflow/0.1",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8", errors="replace"))
    models = [
        str(item.get("id"))
        for item in body.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ]
    return models


def test_openai_compatible(payload):
    base_url, api_key = api_test_config(payload, "image_api")
    if not base_url:
        return {"ok": False, "message": "缺少 Base URL"}
    if not api_key:
        return {"ok": False, "message": "缺少 API Key"}

    # Prefer /models: this is cheap and works for most OpenAI-compatible APIs.
    url = base_url if base_url.endswith("/models") else f"{base_url}/v1/models"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "srt-whiteboard-workflow/0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(2048).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "message": "连接成功" if 200 <= response.status < 300 else body[:300],
                "preview": body[:300],
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "message": api_error_message(exc, api_key)}
    except Exception as exc:
        return {"ok": False, "message": api_error_message(exc, api_key)}


def test_text_models(payload):
    base_url, api_key = api_test_config(payload, "text_api")
    try:
        models = fetch_openai_models(base_url, api_key)
    except Exception as exc:
        return {"ok": False, "message": api_error_message(exc, api_key)}
    if not models:
        return {"ok": False, "message": "接口可访问，但没有返回可选模型。"}
    return {"ok": True, "models": models, "message": f"已读取 {len(models)} 个模型"}


def test_text_completion(payload):
    base_url, api_key = api_test_config(payload, "text_api")
    model = str(payload.get("model") or "").strip()
    if not base_url:
        return {"ok": False, "message": "缺少 Base URL"}
    if not api_key:
        return {"ok": False, "message": "未找到 API Key，请先填写并保存密钥"}
    if not model:
        return {"ok": False, "message": "请先选择或填写文本模型"}
    request_payload = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": "hello"}],
    }
    try:
        response = api_json_request(
            f"{base_url}/v1/chat/completions", request_payload, api_key, timeout=60
        )
    except Exception as exc:
        return {"ok": False, "model": model, "message": api_error_message(exc, api_key)}
    content = extract_text_api_content(response).strip()
    if not content:
        return {
            "ok": False,
            "model": model,
            "message": "接口已响应，但没有返回可显示的文本内容。",
        }
    usage = response.get("usage") if isinstance(response, dict) else None
    usage_summary = {
        key: usage[key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(usage, dict) and key in usage
    }
    return {
        "ok": True,
        "model": response.get("model") or model,
        "reply": content[:4000],
        "usage": usage_summary,
    }


def api_json_request(url, payload, api_key, timeout=180):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "srt-whiteboard-workflow/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(1200).decode("utf-8", errors="replace")
        detail = detail.replace(str(api_key), "[已隐藏 API Key]") if api_key else detail
        raise RuntimeError(f"API 返回 HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API 网络连接失败: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"API 请求超时（{timeout} 秒）") from exc
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        preview = raw[:500].decode("utf-8", errors="replace")
        if api_key:
            preview = preview.replace(str(api_key), "[已隐藏 API Key]")
        raise RuntimeError(f"API 返回内容不是有效 JSON: {preview}") from exc


def text_model_and_config():
    config = load_config().get("text_api", {})
    base_url = normalize_base_url(config.get("base_url"))
    api_key = str(config.get("api_key") or "")
    model = str(config.get("model") or "")
    if not base_url or not api_key:
        raise ValueError("未配置文本理解 API：请在初始化设置中填写文本 API 地址和密钥")
    if not model:
        request = urllib.request.Request(
            f"{base_url}/v1/models",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
        candidates = [str(item.get("id")) for item in body.get("data", []) if item.get("id")]
        model = next(
            (item for item in candidates if not any(token in item.lower() for token in ("image", "dall", "flux", "sd"))),
            candidates[0] if candidates else "",
        )
    if not model:
        raise ValueError("文本理解 API 未返回可用模型，请手动填写模型名称")
    return base_url, api_key, model


def extract_text_api_content(response):
    """Support the common Chat Completions and Responses JSON shapes."""
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        if isinstance(message, dict) and message.get("content"):
            return str(message["content"])
        if choices[0].get("text"):
            return str(choices[0]["text"])
    output = response.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            for content in item.get("content", []) if isinstance(item, dict) else []:
                if isinstance(content, dict) and content.get("text"):
                    parts.append(str(content["text"]))
        if parts:
            return "\n".join(parts)
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    return ""


def understand_scenes_with_text_api(cues, settings, validation_text=None):
    """Ask the configured text model for semantic, contiguous cue groups."""
    base_url, api_key, model = text_model_and_config()
    max_lines = max(1, int(settings.get("max_lines", 3) or 3))
    over_limit = max(0, int(settings.get("over_limit", 10) or 0))
    title_enabled = bool(settings.get("title_recognition"))
    cue_payload = [
        {"index": cue["index"], "start": cue["start"], "end": cue["end"], "text": cue["text"]}
        for cue in cues
    ]
    validation_hint = ""
    if validation_text:
        validation_hint = (
            "\n校验稿仅用于辅助标题判断和语义确认，不要改变 SRT 时间戳。校验稿内容：\n"
            + str(validation_text)[:12000]
        )
    title_rule = (
        "标题识别已开启：判断哪些字幕更适合作为章节/主题标题；标题字幕必须独立成幕，"
        "结合全片结构给出简短的 title_text；标题可以概括或改写该条原字幕，不要机械照抄长句。"
        if title_enabled
        else "标题识别已关闭：所有字幕都按普通叙事内容分组，title_candidate 必须为 false。"
    )
    prompt = f"""你是视频分镜拆幕编辑。请按语义和上下文把下面的 SRT 字幕分成连续的场景组。
规则：
1. 每幕目标是最多 {max_lines} 条字幕，这是软上限，不是固定数量；一幕可以只有 1 条、2 条或 3 条。
2. 不要平均分配，不要为了凑数量切断一个完整观点、因果关系或同一动作。
3. 只有在语义确实必须连在一起时才允许超过 {max_lines} 条；全片最多允许 {over_limit} 个超限分镜。
4. 必须覆盖全部字幕，每条只出现一次；cue_indices 必须连续递增，不能跳号、不能重叠。
5. 保留原始时间范围，不要修改时间戳。{title_rule}
6. 输出严格 JSON，不要 Markdown，不要解释文字：
{{"groups":[{{"cue_indices":[1,2],"title_candidate":false,"title_text":"","reason":"同一观点连续展开"}}]}}

SRT 字幕：
{json.dumps(cue_payload, ensure_ascii=False)}
{validation_hint}
"""
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "你只输出符合要求的 JSON。"},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        response = api_json_request(f"{base_url}/v1/chat/completions", payload, api_key, timeout=180)
    except Exception as exc:
        raise RuntimeError(f"文本理解 API 请求失败：{exc}") from exc
    content = extract_text_api_content(response).strip()
    if not content:
        raise RuntimeError("文本理解 API 返回为空，无法生成拆幕方案")
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    try:
        plan = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"文本理解 API 返回的不是有效 JSON：{content[:300]}") from exc
    if not isinstance(plan, dict) or not isinstance(plan.get("groups"), list):
        raise RuntimeError("文本理解 API 返回缺少 groups 数组，无法安全拆幕")
    return plan, {"model": model, "source": "text_api"}


def image_model_and_config():
    config = load_config().get("image_api", {})
    base_url = normalize_base_url(config.get("base_url"))
    api_key = str(config.get("api_key") or "")
    model = str(config.get("model") or "")
    if not base_url or not api_key:
        raise ValueError("请先在初始化设置中配置画面生成 API")
    if not model:
        # The configured gateway exposes OpenAI-compatible model discovery.
        request = urllib.request.Request(
            f"{base_url}/v1/models",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            models = json.loads(response.read().decode("utf-8", errors="replace")).get("data", [])
        candidates = [str(item.get("id")) for item in models if item.get("id")]
        model = next(
            (item for item in candidates if any(token in item.lower() for token in ("image", "dall", "flux", "sd"))),
            candidates[0] if candidates else "",
        )
    if not model:
        raise ValueError("画面生成 API 未返回可用模型")
    return base_url, api_key, model


_HANDRAW_STYLE_CACHE = None


def handraw_style_entry(value):
    """Resolve a handraw-style preset into its annotated generation data."""
    global _HANDRAW_STYLE_CACHE
    key = str(value or "").strip()
    if not key:
        return None
    if _HANDRAW_STYLE_CACHE is None:
        path = STATIC / "handraw-style" / "skills" / "handdraw-style-prompter" / "references" / "styles.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _HANDRAW_STYLE_CACHE = {str(item.get("number")): item for item in data if item.get("number")}
        except (OSError, ValueError):
            _HANDRAW_STYLE_CACHE = {}
    return _HANDRAW_STYLE_CACHE.get(key)


def scene_prompt(scene, settings):
    text = " ".join(str(cue.get("text") or "").replace("\n", " ") for cue in scene.get("cues", []))
    style = settings.get("style") or "FA-039"
    style_mode = str(settings.get("styleMode") or settings.get("style_mode") or "pure_image")
    if isinstance(settings.get("styleIncludeText"), bool):
        style_mode = "image_text" if settings["styleIncludeText"] else "pure_image"
    whitespace = str(settings.get("whitespace") or "normal")
    style_entry = handraw_style_entry(style)
    if style_entry:
        style = f"{style_entry.get('number')} · {style_entry.get('generation_name') or style_entry.get('reference') or style}"
    custom = str(settings.get("stylePrompt") or "").strip()
    scene_custom = str(scene.get("prompt") or scene.get("visual_prompt") or "").strip()
    asset_hint = ""
    assets = read_visual_assets()
    # Only the visual-reference/style asset participates in static storyboard
    # generation. Pen and paper are runtime composition layers.
    for key in ("style",):
        value = str(settings.get(key) or "").strip()
        for item in assets.values():
            if item.get("key") == key and item.get("value") == value:
                if item.get("prompt"):
                    asset_hint += f"；继承已保存的{key}资产生成逻辑：{item['prompt']}"
                break
    reference_hint = ""
    if settings.get("styleReferenceDataUrl") or settings.get("lastStyleReferencePath"):
        reference_hint = "；需延续项目参考图的线条气质、色彩关系、笔触密度和留白方式，但不要复制参考图中的具体主体或文字。"
    library_hint = ""
    if style_entry:
        library_hint = f"；handraw-style 画风注释：参考来源 {style_entry.get('reference') or '未注明'}；核心特征：{style_entry.get('traits') or '按该编号预设保持一致'}"
    text_mode_hint = (
        "这是纯图模式：画面中绝对不要出现任何文字、字母、数字、标签或水印；只把主题语义转化为图像。"
        if style_mode != "image_text" else
        "这是图文模式：允许且需要把下面的主题原文作为画面构图的一部分，文字必须逐字保留、拼写准确、清晰可读，不添加额外文字、标签或水印。"
    )
    whitespace_hint = {
        "moderate": "留白适中，主体周围保留明显呼吸空间；",
        "high": "大量留白，只呈现解释主题所需的必要主体；",
    }.get(whitespace, "正常留白，主体与背景保持清晰层次；")
    return (
        "生成一张适合白板手绘动画的横版分镜插画，16:9。"
        + text_mode_hint
        + "极简、克制、概念化构图，" + whitespace_hint + "主体清晰，便于之后连续手绘描摹；"
        f"画面参考逻辑：{style}；"
        "使用深灰线稿，少量低饱和红橙蓝点缀，避免复杂背景、照片感、3D 和规则矩形遮罩。"
        f"本幕字幕语义：{text}"
        + (f"；额外风格要求：{custom}" if custom else "")
        + (f"；本幕补充画面要求：{scene_custom}" if scene_custom else "")
        + asset_hint
        + library_hint
        + reference_hint
    )


def image_generation_payload(model, prompt, size, settings=None, include_reference=False, reference_data_url=None):
    """Build a portable OpenAI-compatible image-generation payload."""
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "b64_json",
    }
    if include_reference and settings:
        reference = str(reference_data_url or settings.get("styleReferenceDataUrl") or settings.get("lastComboReferenceDataUrl") or "").strip()
        if reference:
            payload["image"] = reference
    return payload


def project_reference_data_url(folder, settings):
    """Resolve the project's selected reference image without exposing secrets."""
    settings = settings or {}
    direct = str(settings.get("styleReferenceDataUrl") or settings.get("lastComboReferenceDataUrl") or "").strip()
    if direct.startswith("data:image/"):
        return direct
    # A selected custom style is stored in the shared visual asset library;
    # pass that exact image to the image model instead of only its prompt.
    selected_style = str(settings.get("style") or "").strip()
    if selected_style:
        for item in read_visual_assets().values():
            if item.get("key") == "style" and (item.get("value") == selected_style or item.get("name") == selected_style):
                asset_path = visual_asset_root() / str(item.get("path") or item.get("demo_path") or "")
                if asset_path.is_file():
                    try:
                        encoded = base64.b64encode(asset_path.read_bytes()).decode("ascii")
                        mime = "image/webp" if asset_path.suffix.lower() == ".webp" else "image/png"
                        return f"data:{mime};base64,{encoded}"
                    except OSError:
                        pass
    relative = str(settings.get("lastStyleReferencePath") or "").strip().replace("\\", "/")
    if not relative or folder is None:
        return ""
    candidate = (folder / relative).resolve()
    root = folder.resolve()
    if candidate == root or root not in candidate.parents or not candidate.is_file():
        return ""
    try:
        encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
    except OSError:
        return ""
    suffix = candidate.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/webp" if suffix == ".webp" else "image/png"
    return f"data:{mime};base64,{encoded}"


def save_title_board(folder, scene, settings):
    """Create a clean title-card PNG without spending an image-generation call."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("标题页需要 Pillow，请先安装 Pillow") from exc
    ratio = settings.get("ratio", "16:9")
    width, height = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}.get(ratio, (1920, 1080))
    paper = {"暖米黄": (244, 232, 207), "宣纸": (238, 232, 214), "牛皮纸": (201, 167, 124), "灰白纸": (228, 229, 224)}.get(settings.get("paper"), (244, 232, 207))
    image = Image.new("RGB", (width, height), paper)
    draw = ImageDraw.Draw(image)
    title = str(scene.get("title_text") or scene.get("title") or "章节")
    font_name = str(settings.get("subtitleFont") or "Noto Sans SC")
    bundled_font = QIAOMU_FONT_FILES.get(font_name)
    font_candidates = ([str(STATIC / "qiaomu-cover-fonts" / "fonts" / bundled_font)] if bundled_font else []) + [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simfang.ttf",
    ]
    font = next((path for path in font_candidates if Path(path).is_file()), None)
    face = ImageFont.truetype(font, max(34, int(width * 0.045))) if font else ImageFont.load_default()
    box = draw.textbbox((0, 0), title, font=face)
    draw.text(((width - (box[2] - box[0])) / 2, (height - (box[3] - box[1])) / 2), title, fill=(45, 55, 48), font=face)
    target = folder / "boards" / f"scene_{int(scene.get('id', 0)):03d}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return target


def generate_board_image(folder, scene, settings):
    if scene.get("title"):
        target = save_title_board(folder, scene, settings)
        return {
            "path": str(target.relative_to(folder)),
            "provider": "local-title-card",
            "model": None,
            "prompt": f"标题页：{scene.get('title_text') or scene.get('title') or '章节'}",
        }
    base_url, api_key, model = image_model_and_config()
    prompt = scene_prompt(scene, settings)
    payload = image_generation_payload(
        model,
        prompt,
        "1536x864" if settings.get("ratio", "16:9") == "16:9" else "1024x1024",
        settings=settings,
        include_reference=True,
        reference_data_url=project_reference_data_url(folder, settings),
    )
    response = api_json_request(f"{base_url}/v1/images/generations", payload, api_key)
    items = response.get("data") or []
    if not items:
        raise RuntimeError("画面生成 API 返回为空")
    item = items[0]
    target = folder / "boards" / f"scene_{int(scene.get('id', 0)):03d}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    if item.get("b64_json"):
        target.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=120) as response_file:
            target.write_bytes(response_file.read())
    else:
        raise RuntimeError("画面生成 API 未返回图片数据")
    return {"path": str(target.relative_to(folder)), "provider": "openai-compatible", "model": model, "prompt": prompt}

def generate_cover_images(folder, settings):
    base_url, api_key, model = image_model_and_config()
    title = str(settings.get("coverTitle") or "").strip() or "视频封面"
    extra = str(settings.get("coverPrompt") or "").strip()
    prompt = f"生成一张视频封面，标题文字必须清晰准确地显示：{title}。沿用当前项目画风和视觉参考，构图醒目、主体明确，不要加入其他文字或水印。{extra}"
    out = []
    for label, size in (("16x9", "1536x864"), ("4x3", "1152x864")):
        response = api_json_request(f"{base_url}/v1/images/generations", image_generation_payload(model, prompt, size, settings=settings, include_reference=True), api_key)
        item = (response.get("data") or [None])[0]
        if not item: raise RuntimeError(f"封面 {label} 生成接口返回为空")
        target = folder / "cover" / f"cover-{label}.png"; target.parent.mkdir(parents=True, exist_ok=True)
        if item.get("b64_json"): target.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            with urllib.request.urlopen(item["url"], timeout=120) as fh: target.write_bytes(fh.read())
        else: raise RuntimeError(f"封面 {label} 未返回图片")
        out.append({"path": str(target.relative_to(folder)), "url": scene_image_url(settings.get("project_id"), target.relative_to(folder)), "ratio": label, "prompt": prompt})
    return out


def generate_style_reference(settings, kind="组合预览"):
    """Generate a visual reference for the selected pen/style/paper combination."""
    base_url, api_key, model = image_model_and_config()
    pen = settings.get("pen") or "羽毛笔"
    style = settings.get("style") or "极简手绘线稿"
    style_entry = handraw_style_entry(style)
    style_label = f"{style_entry.get('number')} {style_entry.get('generation_name') or style}" if style_entry else style
    paper = settings.get("paper") or "暖米黄"
    focus = {
        "画笔": f"只重点展示一支{pen}，清楚表现笔尖、握持方向、工具材质和落墨后的线条，不要出现其他工具。",
        "羽毛笔": f"只重点展示一支{pen}，清楚表现笔尖、羽毛结构、握持方向和落墨后的线条，不要出现其他工具。",
        "画风": f"只重点展示{style_label}的线条、色彩、明暗和典型笔触，不要出现具体物体、人物或文字。",
        "纸张": f"只重点展示{paper}的纤维、颗粒、底色和自然不规则纹理，画面不要出现画笔或文字。",
        "组合预览": (f"重点展示{pen}的真实绘画笔触与笔尖形象；" if settings.get("penAssetType") != "object" else f"重点展示{settings.get('penObject') or '轨迹物体'}在画面底部接触点产生轨迹的效果；") + f"整体采用{style}；底层是{paper}纸张纹理。",
        "轨迹物体": f"只重点展示一个完整的{settings.get('penObject') or '轨迹物体'}，轨迹绑定在物体正下方接触点，不要出现笔或笔尖。",
    }.get(kind, f"展示{pen}、{style}和{paper}的视觉关系。")
    custom_reference = str(settings.get("styleReferenceDataUrl") or "").strip()
    prompt = (
        "生成一张用于视频制作工具设置页的视觉参考图，不要生成海报，不要出现任何文字、字母、数字、标签或水印。"
        "画面干净、居中、留白充足，适合 UI 卡片预览。"
        f"{focus}"
        "保持低饱和、自然光、平面插画/材质参考风格，避免复杂场景、人物、摄影感和规则矩形遮罩。"
        f"这是{kind}参考图，需要让用户一眼看懂将用于白板手绘动画的视觉效果。"
    )
    if settings.get("stylePrompt"):
        prompt += f"；用户自定义风格要求：{str(settings.get('stylePrompt')).strip()}"
    if kind == "轨迹物体":
        prompt = (
            f"制作一张可直接作为轨迹覆盖层使用的透明 PNG，画面里只有一个完整的{settings.get('penObject') or '轨迹物体'}。"
            "主体必须完整不裁切，背景必须是真正 Alpha 透明 RGBA，不是白色或棋盘格；"
            "禁止出现画笔、笔尖、手、纸张、轨迹线、阴影、文字、水印、边框和其他元素。"
            + (f"用户提示词：{str(settings.get('stylePrompt')).strip()}" if settings.get('stylePrompt') else "")
        )
    if style_entry:
        prompt += f"；该 handraw-style 预设的风格特征：{style_entry.get('traits') or ''}"
    if custom_reference:
        prompt += "；请参考用户上传的风格参考图，提取其构图克制、线条气质和色彩关系，但不要复制其中的文字、人物身份或具体版权元素。"
    request_payload = image_generation_payload(
        model,
        prompt,
        "1024x1024",
        settings=settings,
        include_reference=True,
    )
    if kind == "轨迹物体":
        request_payload["background"] = "transparent"
    # OpenAI-compatible gateways differ in their image-edit input naming.
    # Keep the default generation request portable; pass the reference data
    # only when the provider accepts an image input field.
    if custom_reference:
        request_payload["image"] = custom_reference
    try:
        response = api_json_request(
            f"{base_url}/v1/images/generations",
            request_payload,
            api_key,
        )
        reference_mode = "image+prompt" if custom_reference else "prompt"
    except urllib.error.HTTPError:
        if not custom_reference:
            raise
        request_payload.pop("image", None)
        response = api_json_request(
            f"{base_url}/v1/images/generations",
            request_payload,
            api_key,
        )
        reference_mode = "prompt_only_reference_not_supported"
    items = response.get("data") or []
    if not items:
        raise RuntimeError("视觉参考图 API 返回为空")
    item = items[0]
    if item.get("b64_json"):
        encoded = item["b64_json"]
    elif item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=120) as response_file:
            encoded = base64.b64encode(response_file.read()).decode("ascii")
    else:
        raise RuntimeError("视觉参考图 API 未返回图片数据")
    return {
        "data_url": f"data:image/png;base64,{encoded}",
        "model": model,
        "prompt": prompt,
        "kind": kind,
        "pen": pen,
        "style": style,
        "paper": paper,
        "reference_mode": reference_mode,
    }


def generate_project_boards(folder, project_id, scenes, settings, selected_ids=None, on_progress=None, control=None):
    """Generate only the explicitly requested scenes; failures remain manual."""
    selected = None if selected_ids is None else {str(item) for item in selected_ids}
    total = sum(
        1 for item in scenes
        if selected is None or str((item or {}).get("id") or (item or {}).get("scene_id") or "") in selected
    )
    results = []
    for raw_scene in scenes:
        if control:
            while control.get("paused") and not control.get("cancelled"):
                time.sleep(0.2)
            if control.get("cancelled"):
                break
        scene = dict(raw_scene or {})
        scene_id = str(scene.get("id") or scene.get("scene_id") or "")
        if selected is not None and scene_id not in selected:
            result = {
                "id": scene_id,
                "image_status": scene.get("image_status", "pending"),
                "image_path": scene.get("image_path", ""),
                "skipped": True,
            }
            results.append(result)
            if on_progress:
                on_progress("scene_completed", scene, result, len(results), total)
            continue
        scene["image_status"] = "generating"
        for index, original in enumerate(scenes):
            original_id = str((original or {}).get("id") or (original or {}).get("scene_id") or "")
            if original_id == scene_id:
                scenes[index] = scene
                break
        if on_progress:
            on_progress("scene_started", scene, None, len(results), total)
        try:
            generated = generate_board_image(folder, scene, settings)
            scene.update({
                "image_status": "ready",
                "image_path": generated["path"],
                "image_provider": generated.get("provider"),
                "image_model": generated.get("model"),
                "image_prompt": generated.get("prompt", ""),
                "image_error": "",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            })
            result = {
                "id": scene_id,
                "image_status": "ready",
                "image_path": scene["image_path"],
                "image_model": scene.get("image_model"),
                "image_url": scene_image_url(project_id, scene["image_path"]),
            }
        except Exception as exc:
            # Deliberately no retry: the UI exposes this scene for manual rerun.
            scene.update({
                "image_status": "failed",
                "image_error": str(exc),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            })
            result = {"id": scene_id, "image_status": "failed", "error": str(exc)}
        results.append(result)
        # Keep the updated object in the caller's list even on an individual failure.
        for index, original in enumerate(scenes):
            original_id = str((original or {}).get("id") or (original or {}).get("scene_id") or "")
            if original_id == scene_id:
                scenes[index] = scene
                break
        if on_progress:
            on_progress("scene_completed", scene, result, len(results), total)
    return results


def _board_job_snapshot(job):
    return {
        "ok": True,
        "job_id": job["job_id"],
        "project_id": job["project_id"],
        "status": job["status"],
        "completed": job["completed"],
        "total": job["total"],
        "current_scene_id": job.get("current_scene_id"),
        "scenes": deepcopy(job["scenes"]),
        "results": deepcopy(job["results"]),
        "error": job.get("error", ""),
    }


def _persist_board_progress(job, final=False):
    folder = safe_project_path(job["project_id"])
    if folder is None:
        return
    state = read_project_state(folder)
    scenes = deepcopy(job["scenes"])
    results = deepcopy(job["results"])
    state["scenes"] = scenes
    state["boards"] = {
        "status": job["status"] if final else "generating",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "completed": job["completed"],
        "total": job["total"],
        "current_scene_id": job.get("current_scene_id"),
    }
    if final:
        state["stage"] = "boards"
    write_project_state(folder, state)
    write_json_recoverable(folder / "scenes.json", {"cues": state.get("cues", []), "scenes": scenes, "validation": state.get("validation", {})})


def _run_board_job(job_id):
    with _BOARD_JOBS_LOCK:
        job = _BOARD_JOBS.get(job_id)
    if not job:
        return

    def on_progress(event, scene, result, completed, total):
        with _BOARD_JOBS_LOCK:
            job["completed"] = sum(1 for item in job["results"] if not item.get("skipped"))
            job["current_scene_id"] = scene.get("id") if event == "scene_started" else None
            if event == "scene_completed":
                job["results"].append(deepcopy(result))
                job["completed"] = sum(1 for item in job["results"] if not item.get("skipped"))
            job["scenes"] = deepcopy(job["source_scenes"])
        _persist_board_progress(job)

    try:
        _persist_board_progress(job)
        generate_project_boards(
            job["folder"], job["project_id"], job["source_scenes"], job["settings"], job["selected_ids"], on_progress, job
        )
        with _BOARD_JOBS_LOCK:
            all_ready = all(item.get("image_status") in {"ready", "skipped"} for item in job["source_scenes"])
            job["status"] = "cancelled" if job.get("cancelled") else ("ready" if all_ready else "partial")
            job["current_scene_id"] = None
            job["scenes"] = deepcopy(job["source_scenes"])
        _persist_board_progress(job, final=True)
        update_project({"id": job["project_id"], "stage": "boards", "scene_count": len(job["source_scenes"])})
    except Exception as exc:
        with _BOARD_JOBS_LOCK:
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["current_scene_id"] = None
            job["scenes"] = deepcopy(job["source_scenes"])
        _persist_board_progress(job, final=True)


def _animation_job_snapshot(job):
    return {
        "ok": True,
        "job_id": job["job_id"],
        "project_id": job["project_id"],
        "status": job["status"],
        "completed": job["completed"],
        "total": job["total"],
        "current_scene_id": job.get("current_scene_id"),
        "started_at": job["started_at"],
        "scenes": deepcopy(job["scenes"]),
        "results": deepcopy(job["results"]),
        "error": job.get("error", ""),
    }


def _persist_animation_progress(job, final=False):
    folder = safe_project_path(job["project_id"])
    if folder is None:
        return
    state = read_project_state(folder)
    scenes = deepcopy(job["scenes"])
    state["scenes"] = scenes
    state["animation"] = {
        "status": job["status"] if final else "rendering",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "results": deepcopy(job["results"]),
        "mode": "full_frame_stream",
        "completed": job["completed"],
        "total": job["total"],
        "current_scene_id": job.get("current_scene_id"),
        "started_at": job["started_at"],
    }
    if final:
        state["stage"] = "animation"
    write_project_state(folder, state)
    write_json_recoverable(folder / "scenes.json", {"cues": state.get("cues", []), "scenes": scenes, "validation": state.get("validation", {})})


def _run_animation_job(job_id):
    with _ANIMATION_JOBS_LOCK:
        job = _ANIMATION_JOBS.get(job_id)
    if not job:
        return

    def on_progress(event, scene, result, completed, total):
        with _ANIMATION_JOBS_LOCK:
            scene_id = str(scene.get("id") or scene.get("scene_id") or "")
            for index, original in enumerate(job["source_scenes"]):
                if str(original.get("id") or original.get("scene_id") or "") == scene_id:
                    job["source_scenes"][index] = {**original, **deepcopy(scene)}
                    break
            job["scenes"] = deepcopy(job["source_scenes"])
            job["current_scene_id"] = scene.get("id") if event == "scene_started" else None
            if event == "scene_completed":
                job["results"].append(deepcopy(result))
                job["completed"] = sum(1 for item in job["results"] if not item.get("skipped"))
        _persist_animation_progress(job)

    try:
        _persist_animation_progress(job)
        result = render_project_animations(
            job["folder"], job["project_id"], job["source_scenes"], job["settings"], job["selected_ids"], on_progress
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("message") or "动画渲染未完成")
        with _ANIMATION_JOBS_LOCK:
            job["source_scenes"] = deepcopy(result["scenes"])
            job["scenes"] = deepcopy(result["scenes"])
            job["current_scene_id"] = None
            job["status"] = "ready" if all(item.get("animation_status") in {"ready", "skipped"} for item in result["scenes"]) else "partial"
        _persist_animation_progress(job, final=True)
        update_project({"id": job["project_id"], "stage": "animation", "scene_count": len(result["scenes"])})
    except Exception as exc:
        with _ANIMATION_JOBS_LOCK:
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["current_scene_id"] = None
            job["scenes"] = deepcopy(job["source_scenes"])
        _persist_animation_progress(job, final=True)


def save_style_reference(folder, result, filename):
    """Persist a generated style reference and return a project-relative path."""
    if not result.get("data_url"):
        raise RuntimeError("参考图没有可保存的图片数据")
    match = re.match(r"data:image/[^;]+;base64,(.+)", result["data_url"], re.S)
    if not match:
        raise RuntimeError("参考图数据格式无效")
    target = folder / "references" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(match.group(1)))
    return str(target.relative_to(folder))


def preset_slug(value):
    """Create a stable, filesystem-safe name for a visual preset."""
    raw = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(value or "").strip(), flags=re.UNICODE)
    return raw.strip("-")[:60] or "preset"


def preset_reference_prompt(key, value, kind, settings):
    """Build a deterministic prompt for one real preset reference image."""
    value = str(value or "").strip()
    kind = str(kind or "").strip()
    if key == "style":
        library_style = handraw_style_entry(value)
        if library_style:
            style_mode = str((settings or {}).get("styleMode") or (settings or {}).get("style_mode") or "pure_image")
            if isinstance((settings or {}).get("styleIncludeText"), bool):
                style_mode = "image_text" if settings["styleIncludeText"] else "pure_image"
            mode_hint = "纯图模式，不生成画中文字；" if style_mode != "image_text" else "图文模式，保留主题原文并让文字参与构图；"
            return (
                f"生成一张横向 16:9 的完整画面参考图，严格采用 handraw-style {library_style.get('number')}「{library_style.get('generation_name') or value}」预设。"
                f"参考来源：{library_style.get('reference') or '未注明'}。核心风格特征：{library_style.get('traits') or '保持该编号预设的线条、构图和色彩语言'}。"
                f"{mode_hint}画面服务于白板手绘动画分镜，主体清晰、构图便于连续描摹，不能出现额外标签、水印或 UI。"
            )
    if key == "style" and value in STYLE_REFERENCE_PROMPTS:
        return STYLE_REFERENCE_PROMPTS[value]
    if key == "paper" and value in PAPER_REFERENCE_PROMPTS:
        return PAPER_REFERENCE_PROMPTS[value]
    common = (
        "生成一张用于白板手绘动画制作工具设置页的真实视觉参考图，"
        "1:1 构图，主体居中，留白充足，低饱和自然光，清晰可辨，"
        "绝对不要出现文字、字母、数字、标签、水印、Logo、人物或复杂场景。"
        "这是一张素材参考卡，不是海报，不是 UI 截图，不要拼贴多个选项。"
    )
    if key == "pen":
        detail = (
            f"只展示一支{value}及其笔尖、握持方向和落墨后的连续线条。"
            "笔尖必须清晰位于画面前端，线条质感真实，背景保持干净纸面，"
            "不要出现其他画笔、颜料或文字。"
        )
    elif key == "style":
        detail = (
            f"以一幅完整的画面参考展示{value}的生成逻辑：包含一个清晰但简洁的叙事主体、"
            "前中后景关系、主体动作、构图重心、留白比例、线条、明暗、色彩关系、颗粒和笔触。"
            "这不是抽象色板，也不是单独笔触样本；要让用户能看懂未来分镜会如何组织主体与场景。"
        )
    elif key == "paper":
        detail = (
            f"只展示{value}纸张的底色、纤维、颗粒、折痕和自然不规则纹理。"
            "画面是平铺纸面材质特写，不要出现画笔、手、文字或规则矩形遮罩。"
        )
    else:
        detail = f"展示{value}作为{kind}的视觉特征。"
    custom = str((settings or {}).get("stylePrompt") or "").strip()
    return common + detail + (f"用户补充要求：{custom}" if custom else "")


def preset_production_prompt(key, value, kind, settings):
    """Prompt for the clean production-layer asset, separate from the UI demo."""
    if key in {"penObject", "object"} or (key == "pen" and str((settings or {}).get("penAssetType") or "pen") == "object"):
        return (
            f"制作一张可直接叠加到视频中的独立轨迹物体素材：画面里只有一个完整的{value}。"
            "主体必须完整、不裁切，姿态清楚，运动方向明确；轨迹绑定点位于物体正下方与地面接触的位置。"
            "不要把它改成笔、画笔、手或工具，不要生成笔尖。"
            "背景必须是真正的 Alpha 透明，不是白色、灰色或棋盘格底图；输出 RGBA PNG。"
            "除该物体外禁止出现手、纸张、轨迹线、阴影、文字、字母、数字、水印、边框或其他元素。"
        )
    if key == "pen":
        return (
            f"制作一张可直接叠加到视频中的独立画笔素材：画面里只有一支完整的{value}。"
            "笔身从末端到尖端全部完整显示，严格居中并沿左上至右下方向斜放，"
            "主体占画布高度约 80%，四周保留均匀透明留白，任何部分都不得裁切。"
            "清楚表现真实可辨的笔尾、笔身、连接部位和笔尖；笔尖必须是明确的书写端，"
            "位于画面右下方向并清晰尖锐，不能把羽毛尖或笔杆尾端误作落笔端。"
            "使用干净、自然、可信的产品绘制效果，轮廓完整，结构合理，比例协调。"
            "背景必须是真正的 Alpha 透明，不是白色、灰色或棋盘格底图；输出 RGBA PNG。"
            "除这一支笔本身外，禁止出现手、纸张、墨迹、笔触样本、线条、颜料、桌面、"
            "阴影、投影、反光底板、文字、字母、数字、标注、水印、边框或任何其他元素。"
        )
    if key == "style":
        return (
            f"生成一张完整的{value}画面参考图，保留一个简洁叙事主体、构图关系、"
            "动作逻辑、留白比例、线条与色彩语言，用作后续分镜生成的视觉逻辑参考；"
            "不要做成单独色板或笔触样本，不出现文字、水印或 UI。"
        )
    return (
        f"生成一张{value}纸张材质参考图，展示底色、纤维、颗粒和自然纹理，"
        "不出现画笔、手、文字、规则遮罩或其他物体。"
    )


def generate_preset_reference(settings, key, value, kind, transparent=False):
    """Generate one preset asset without changing the selected project setting."""
    base_url, api_key, model = image_model_and_config()
    prompt = (
        preset_production_prompt(key, value, kind, settings)
        if transparent and key in {"pen", "penObject"}
        else preset_reference_prompt(key, value, kind, settings)
    )
    size = "1536x864" if key in {"style", "paper"} else "1024x1024"
    request_payload = image_generation_payload(model, prompt, size)
    if transparent:
        request_payload["background"] = "transparent"
    response = api_json_request(
        f"{base_url}/v1/images/generations",
        request_payload,
        api_key,
    )
    items = response.get("data") or []
    if not items:
        raise RuntimeError("预设参考图 API 返回为空")
    item = items[0]
    if item.get("b64_json"):
        encoded = item["b64_json"]
    elif item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=120) as response_file:
            encoded = base64.b64encode(response_file.read()).decode("ascii")
    else:
        raise RuntimeError("预设参考图 API 未返回图片数据")
    return {
        "data_url": f"data:image/png;base64,{encoded}",
        "model": model,
        "prompt": prompt,
        "kind": kind,
        "key": key,
        "value": value,
        "transparent": bool(transparent),
    }


def save_preset_reference(folder, result, key, value):
    """Save a preset under references/presets and return its project-relative path."""
    filename = f"{preset_slug(key)}-{preset_slug(value)}.png"
    return save_style_reference(folder, result, f"presets/{filename}")


def persist_preset_reference(folder, result, path):
    state = read_project_state(folder)
    refs = state.setdefault("preset_references", {})
    ref_key = f"{result.get('key')}:{result.get('value')}"
    refs[ref_key] = {
        "key": result.get("key"),
        "value": result.get("value"),
        "kind": result.get("kind"),
        "path": path,
        "model": result.get("model"),
        "prompt": result.get("prompt"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_project_state(folder, state)
    return refs[ref_key]


def project_file_path(folder, relative_path):
    """Resolve a project-relative file without allowing path traversal."""
    raw = str(relative_path or "").replace("\\", "/")
    candidate = (folder / raw).resolve()
    root = folder.resolve()
    if candidate == root or root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def scene_image_url(project_id, relative_path):
    if not project_id or not relative_path:
        return None
    return f"/api/projects/{project_id}/file?path={quote(str(relative_path))}"


def recover_scene_board_assets(folder, scenes):
    """Restore generated board references from saved metadata or board files."""
    recovered = []
    for raw in scenes or []:
        scene = dict(raw or {})
        scene_id = scene.get("id") or scene.get("scene_id")
        relative = str(scene.get("image_path") or scene.get("imagePath") or "").replace("\\", "/")
        target = project_file_path(folder, relative) if relative else None
        if target is None:
            try:
                candidate = folder / "boards" / f"scene_{int(scene_id):03d}.png"
            except (TypeError, ValueError):
                candidate = None
            if candidate and candidate.is_file():
                relative = str(candidate.relative_to(folder)).replace("\\", "/")
                target = candidate
        if target is not None and target.is_file():
            scene["image_path"] = relative
            scene["image_status"] = "ready"
            scene["image_error"] = ""
        recovered.append(scene)
    return recovered


def prepare_merged_subtitles(folder, origin_ms, finish_ms):
    """Create a project-local SRT shifted to the video timeline.

    The imported SRT remains the source of truth.  We only shift timestamps
    when the rendered video starts at the first cue rather than at 00:00:00.
    """
    state = read_project_state(folder)
    source = state.get("inputs", {}).get("srt", {}) or {}
    source_path = Path(str(source.get("path") or ""))
    if not source_path.is_file():
        return None
    try:
        cues = parse_srt_text(source_path.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return None
    if not cues:
        return None
    output_dir = folder / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "merged_subtitles.srt"
    blocks = []
    for index, cue in enumerate(cues, 1):
        start = max(0, int(cue["start_ms"]) - int(origin_ms))
        end = max(start + 1, int(cue["end_ms"]) - int(origin_ms))
        if finish_ms is not None:
            if start >= int(finish_ms):
                continue
            end = min(end, int(finish_ms))
        blocks.append(
            f"{index}\n{format_timecode(start)} --> {format_timecode(end)}\n{cue['text']}\n"
        )
    if not blocks:
        return None
    target.write_text("\n".join(blocks), encoding="utf-8")
    return target


def _ass_color(value, alpha="00"):
    raw = str(value or "").strip().lstrip("#")
    if len(raw) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        raw = "FFFFFF"
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    return f"&H{alpha}{bb}{gg}{rr}"


QIAOMU_FONT_FILES = {
    "Noto Sans SC": "notosans.woff2", "Noto Sans SC Bold": "notosans-bold.woff2", "Noto Sans SC Black": "notosans-heavy.woff2",
    "Noto Serif SC": "notoserif.woff2", "Noto Serif SC Bold": "notoserif-bold.woff2", "Noto Serif SC Black": "notoserif-heavy.woff2",
    "Zhuque Fangsong": "zhuque.woff2", "LXGW WenKai": "wenkai.woff2", "Smiley Sans": "smiley.woff2", "Douyin Sans": "douyin.ttf",
    "Glow Sans SC Heavy": "glow.woff2", "ZCOOL QingKe HuangYou": "huangyou.woff2", "ZCOOL KuaiLe": "kuaile.woff2",
    "JiangCheng YuanTi": "jiangcheng.woff2", "Maoken Assorted Sans": "maoken.woff2", "Tiejili SC": "tiejili.woff2",
    "Ma Shan Zheng": "mashan.woff2", "Zhi Mang Xing": "zhimang.woff2", "Fusion Pixel": "pixel.woff2",
    "Inter": "inter.woff2", "Space Grotesk": "spacegrotesk.woff2", "Bricolage Grotesque": "bricolage.woff2",
    "Unbounded": "unbounded.woff2", "Anton": "anton.woff2", "Bebas Neue": "bebas.woff2", "Archivo Black": "archivoblack.woff2",
    "DM Serif Display": "dmserif.woff2", "Fraunces": "fraunces.woff2", "Instrument Serif": "instrument.woff2",
    "Cormorant Garamond": "cormorant.woff2", "JetBrains Mono": "jetbrains.woff2", "Caveat": "caveat.woff2",
}


def subtitle_filter(subtitle_path, settings=None):
    """Return a visible FFmpeg subtitles filter using the project subtitle settings."""
    settings = settings or {}
    path = str(subtitle_path).replace("\\", "/").replace(":", "\\:")
    font = str(settings.get("subtitleFont") or "Noto Sans SC")
    font_map = {
        "思源黑体": "Noto Sans SC", "思源宋体": "Noto Serif SC", "微软雅黑": "Noto Sans SC", "霞鹜文楷": "LXGW WenKai",
        "Noto Sans SC Bold": "Noto Sans SC", "Noto Sans SC Black": "Noto Sans SC",
        "Noto Serif SC Bold": "Noto Serif SC", "Noto Serif SC Black": "Noto Serif SC",
    }
    font = font_map.get(font, font)
    pos = str(settings.get("subtitlePosition") or "底部居中")
    alignment = {"顶部居中": 8, "中部居中": 5, "底部居中": 2, "左下": 1, "右下": 3}.get(pos, 2)
    size = max(10, min(120, int(settings.get("subtitleSize") or 28)))
    margin_v = max(10, min(300, int(settings.get("subtitleMargin") or 48)))
    outline = max(0, min(12, int(settings.get("subtitleOutline") or 2)))
    bold = -1 if settings.get("subtitleBold") else 0
    primary = _ass_color(settings.get("subtitleColor") or "#26382f")
    outline_color = _ass_color(settings.get("subtitleOutlineColor") or "#FFFFFF")
    border_style = 3 if settings.get("subtitleBackground") == "实色底框" else 1
    back = _ass_color(settings.get("subtitleBackgroundColor") or "#FFFDF4", "20" if border_style == 3 else "FF")
    force = (
        f"FontName={font},FontSize={size},PrimaryColour={primary},"
        f"OutlineColour={outline_color},BackColour={back},Bold={bold},"
        f"Alignment={alignment},MarginV={margin_v},Outline={outline},Shadow=0,"
        f"BorderStyle={border_style}"
    )
    fonts_dir = str((STATIC / "qiaomu-cover-fonts" / "fonts").resolve()).replace("\\", "/").replace(":", "\\:")
    return f"subtitles='{path}':fontsdir='{fonts_dir}':force_style='{force}'"


def ffmpeg_status():
    # A long-running local server may have started before the user installed
    # FFmpeg. Refresh Windows user/machine PATH on every check instead of
    # trusting the process environment captured at startup.
    search_path = os.environ.get("PATH", "")
    if sys.platform == "win32":
        try:
            import winreg

            paths = []
            for hive, subkey in (
                (winreg.HKEY_CURRENT_USER, r"Environment"),
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            ):
                try:
                    value, _ = winreg.QueryValueEx(winreg.OpenKey(hive, subkey), "Path")
                    paths.append(value)
                except OSError:
                    pass
            search_path = os.pathsep.join([search_path, *paths])
        except Exception:
            pass

    def locate(name):
        hit = shutil.which(name, path=search_path)
        if hit:
            return hit
        if sys.platform == "win32":
            candidates = [
                Path(r"C:\ffmpeg\bin") / f"{name}.exe",
                Path(r"C:\Program Files\ffmpeg\bin") / f"{name}.exe",
                Path(r"C:\ProgramData\chocolatey\bin") / f"{name}.exe",
            ]
            for candidate in candidates:
                if candidate.is_file():
                    return str(candidate)
        return None

    ffmpeg = locate("ffmpeg")
    ffprobe = locate("ffprobe")
    result = {
        "ffmpeg": {"installed": bool(ffmpeg), "path": ffmpeg},
        "ffprobe": {"installed": bool(ffprobe), "path": ffprobe},
        "ready": bool(ffmpeg and ffprobe),
    }
    return result


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, data):
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self):
        request_path = urlparse(self.path).path
        if request_path == "/api/workflow/generate-boards/status":
            query = parse_qs(urlparse(self.path).query)
            job_id = (query.get("job_id") or [""])[0]
            with _BOARD_JOBS_LOCK:
                job = _BOARD_JOBS.get(job_id)
                snapshot = _board_job_snapshot(job) if job else None
            if snapshot is None:
                return self._json(404, {"ok": False, "message": "分镜生成任务不存在或本地服务已重启，请检查项目中的已生成图片。"})
            for scene in snapshot["scenes"]:
                image_path = scene.get("image_path") or scene.get("imagePath")
                if image_path:
                    scene["image_url"] = scene_image_url(snapshot["project_id"], image_path)
            return self._json(200, snapshot)
        if request_path == "/api/workflow/generate-animation/status":
            query = parse_qs(urlparse(self.path).query)
            job_id = (query.get("job_id") or [""])[0]
            with _ANIMATION_JOBS_LOCK:
                job = _ANIMATION_JOBS.get(job_id)
                snapshot = _animation_job_snapshot(job) if job else None
            if snapshot is None:
                return self._json(404, {"ok": False, "message": "动画渲染任务不存在或本地服务已重启，请检查项目中的已生成视频。"})
            for scene in snapshot["scenes"]:
                animation_path = scene.get("animation_path")
                if animation_path:
                    scene["animation_url"] = scene_image_url(snapshot["project_id"], animation_path)
            return self._json(200, snapshot)
        if request_path == "/api/config":
            config = load_config()
            for section in ("text_api", "image_api"):
                if section in config:
                    config[section] = dict(config[section])
                    config[section]["api_key_configured"] = bool(config[section].get("api_key"))
                    if config[section].get("api_key"):
                        config[section]["api_key"] = "••••••" + config[section]["api_key"][-4:]
            return self._json(200, config)
        if request_path == "/api/ffmpeg":
            return self._json(200, ffmpeg_status())
        if request_path == "/api/projects":
            return self._json(200, list_projects())
        if request_path == "/api/project-root":
            return self._json(200, {"root": str(project_root())})
        if request_path == "/api/assets/visual-presets":
            assets = visual_asset_catalog()
            assets.update(read_visual_assets())
            for asset_id, item in read_visual_assets().items():
                asset = dict(item or {})
                if asset.get("path"):
                    asset["url"] = f"/api/assets/file?path={quote(str(asset['path']))}"
                    asset["exists"] = visual_asset_file(asset["path"]) is not None
                assets[asset_id] = asset
            return self._json(200, {"ok": True, "assets": assets})
        if request_path == "/api/assets/file":
            query = parse_qs(urlparse(self.path).query)
            target = visual_asset_file(unquote(query.get("path", [""])[0]))
            if target is None:
                return self._json(404, {"ok": False, "message": "资产文件不存在"})
            content_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_types.get(target.suffix.lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if request_path.startswith("/api/projects/") and request_path.endswith("/state"):
            project_id = unquote(request_path.split("/")[3])
            folder = safe_project_path(project_id)
            if folder is None:
                return self._json(404, {"ok": False, "message": "项目不存在"})
            state = read_project_state(folder)
            if state.get("scenes"):
                state["scenes"] = recover_scene_board_assets(folder, state["scenes"])
                if state.get("scenes") and all(scene.get("image_status") == "ready" for scene in state["scenes"]):
                    state.setdefault("boards", {})["status"] = "ready"
            return self._json(200, {"ok": True, "state": state})
        if request_path.startswith("/api/projects/") and request_path.rstrip("/").split("/")[-1] == "preset-references":
            project_id = unquote(request_path.split("/")[3])
            folder = safe_project_path(project_id)
            if folder is None:
                return self._json(404, {"ok": False, "message": "项目不存在"})
            state = read_project_state(folder)
            references = {}
            for ref_key, item in (state.get("preset_references") or {}).items():
                ref = dict(item or {})
                if ref.get("path"):
                    ref["url"] = scene_image_url(project_id, ref["path"])
                    ref["exists"] = project_file_path(folder, ref["path"]) is not None
                references[ref_key] = ref
            return self._json(200, {"ok": True, "references": references})
        if request_path.startswith("/api/projects/") and request_path.endswith("/file"):
            project_id = unquote(request_path.split("/")[3])
            folder = safe_project_path(project_id)
            if folder is None:
                return self._json(404, {"ok": False, "message": "项目不存在"})
            query = parse_qs(urlparse(self.path).query)
            target = project_file_path(folder, unquote(query.get("path", [""])[0]))
            if target is None:
                return self._json(404, {"ok": False, "message": "项目文件不存在"})
            content_types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".mp4": "video/mp4",
                ".json": "application/json; charset=utf-8",
            }
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_types.get(target.suffix.lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        path = STATIC / ("index.html" if request_path in ("/", "") else request_path.lstrip("/"))
        if path.is_file() and STATIC in path.resolve().parents:
            data = path.read_bytes()
            content_types = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".svg": "image/svg+xml",
                ".woff": "font/woff",
                ".woff2": "font/woff2",
                ".ttf": "font/ttf",
                ".otf": "font/otf",
            }
            self.send_response(200)
            self.send_header(
                "Content-Type",
                content_types.get(path.suffix.lower(), "application/octet-stream"),
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        try:
            payload = self._body()
            if self.path == "/api/config":
                save_config(payload)
                return self._json(200, {"ok": True, "message": "本地配置已保存"})
            if self.path == "/api/test-text":
                return self._json(200, test_text_completion(payload))
            if self.path == "/api/text-models":
                return self._json(200, test_text_models(payload))
            if self.path == "/api/test-image":
                return self._json(200, test_openai_compatible(payload))
            if self.path == "/api/projects":
                meta, error = create_project(payload.get("name"))
                if error:
                    return self._json(400, {"ok": False, "message": error})
                return self._json(201, {"ok": True, "project": meta})
            if self.path == "/api/project-root":
                raw_path = str(payload.get("root") or "").strip()
                if not raw_path:
                    return self._json(400, {"ok": False, "message": "请选择有效的项目根目录"})
                selected = Path(raw_path).expanduser()
                if not selected.is_absolute():
                    return self._json(400, {"ok": False, "message": "项目根目录必须是绝对路径"})
                normalized = str(selected.resolve())
                current = load_config()
                current["project_root"] = normalized
                save_config(current)
                return self._json(200, {"ok": True, "root": normalized, "message": "项目根目录已保存"})
            if self.path == "/api/projects/update":
                ok = update_project(payload)
                return self._json(200 if ok else 404, {"ok": ok, "message": "项目状态已更新" if ok else "项目不存在或无法更新"})
            if self.path == "/api/workflow/import":
                project_id = payload.get("project_id")
                folder = safe_project_path(project_id)
                if folder is None:
                    return self._json(404, {"ok": False, "message": "项目不存在"})
                folder.mkdir(parents=True, exist_ok=True)
                imported = {}
                for role in ("srt", "validation", "audio"):
                    name, content = decode_upload(payload.get(role))
                    if content is None:
                        continue
                    suffix = Path(name).suffix.lower()
                    allowed = {
                        "srt": {".srt", ".txt"},
                        "validation": {".srt", ".txt"},
                        "audio": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"},
                    }[role]
                    if suffix not in allowed:
                        return self._json(400, {"ok": False, "message": f"{role} 文件类型不支持: {suffix}"})
                    target_dir = folder / "input"
                    target_dir.mkdir(exist_ok=True)
                    target = target_dir / name
                    target.write_bytes(content)
                    imported[role] = {
                        "name": name,
                        "path": str(target),
                        "size": len(content),
                    }
                state = read_project_state(folder)
                state.setdefault("project_id", project_id)
                state.setdefault("stage", "import")
                state["inputs"] = {**state.get("inputs", {}), **imported}
                write_project_state(folder, state)
                return self._json(200, {"ok": True, "inputs": imported, "state": state})
            if self.path == "/api/workflow/parse":
                project_id = payload.get("project_id")
                folder = safe_project_path(project_id) if project_id else None
                if project_id and folder is None:
                    return self._json(404, {"ok": False, "message": "项目不存在"})
                srt_text = payload.get("srt_text")
                validation_text = payload.get("validation_text")
                if not srt_text:
                    input_info = read_project_state(folder).get("inputs", {}).get("srt", {})
                    input_path = Path(input_info.get("path", "")) if input_info else None
                    if input_path and input_path.is_file():
                        srt_text = input_path.read_text(encoding="utf-8-sig", errors="replace")
                if not srt_text:
                    return self._json(400, {"ok": False, "message": "请先导入 SRT 文件"})
                cues = parse_srt_text(srt_text)
                if not cues:
                    return self._json(400, {"ok": False, "message": "未解析到有效字幕条，请检查 SRT 格式"})
                validation = validate_cues(cues, validation_text)
                settings = payload.get("settings") or {}
                try:
                    semantic_plan, model_meta = understand_scenes_with_text_api(cues, settings, validation_text)
                except Exception as exc:
                    return self._json(502, {
                        "ok": False,
                        "stage": "import",
                        "code": "TEXT_API_FAILED",
                        "message": "字幕拆幕未完成：文本理解 API 调用失败",
                        "details": str(exc),
                        "project_id": project_id,
                        "cue_count": len(cues),
                        "hint": "请检查初始化设置中的文本 API 地址、模型和连接测试；未使用本地平均切分替代。",
                        "validation": validation,
                    })
                scenes, scene_result = build_scenes(
                    cues,
                    settings.get("max_lines", 3),
                    settings.get("over_limit", 10),
                    settings.get("title_recognition", False),
                    semantic_plan=semantic_plan,
                )
                if not scene_result["ok"]:
                    return self._json(422, {
                        "ok": False,
                        "stage": "import",
                        "code": scene_result.get("code", "SCENE_SPLIT_FAILED"),
                        "message": scene_result["message"],
                        "details": f"拆幕来源：{scene_result.get('source', 'text_api')}；超限幕数：{scene_result.get('over_limit_count', 0)}；允许：{settings.get('over_limit', 10)}",
                        "project_id": project_id,
                        "validation": validation,
                        "scenes": scenes,
                    })
                timeline = {
                    "start_ms": cues[0]["start_ms"],
                    "end_ms": cues[-1]["end_ms"],
                    "duration_ms": cues[-1]["end_ms"] - cues[0]["start_ms"],
                }
                if folder is not None:
                    state = read_project_state(folder)
                    state.update({
                        "project_id": project_id,
                        "stage": "import",
                        "cues": cues,
                        "scenes": scenes,
                        "validation": validation,
                        "split_settings": settings,
                        "split_model": model_meta,
                        "render_mode": settings.get("render_mode", "手绘动画"),
                        "timeline": timeline,
                    })
                    write_json_recoverable(folder / "scenes.json", {"cues": cues, "scenes": scenes, "validation": validation})
                    try:
                        write_project_state(folder, state)
                        update_project({"id": project_id, "stage": "boards", "subtitle_count": len(cues), "scene_count": len(scenes)})
                    except PermissionError:
                        # Do not discard a successful parse because project metadata is locked.
                        pass
                return self._json(200, {"ok": True, "cue_count": len(cues), "scene_count": len(scenes), "validation": validation, "scenes": scenes, "timeline": timeline, "split_model": model_meta})
            if self.path == "/api/workflow/generate-style-reference":
                project_id = payload.get("project_id")
                folder = safe_project_path(project_id) if project_id else None
                settings = payload.get("settings") or {}
                kind = str(payload.get("kind") or "组合预览")
                if kind not in {"组合预览", "画笔", "羽毛笔", "画风", "纸张", "轨迹物体"}:
                    return self._json(400, {"ok": False, "message": "不支持的参考图类型"})
                result = generate_style_reference(settings, kind)
                saved_path = None
                if folder is not None:
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    safe_kind = {"组合预览": "combo", "画笔": "pen", "羽毛笔": "pen", "画风": "style", "纸张": "paper", "轨迹物体": "object"}[kind]
                    saved_path = save_style_reference(folder, result, f"style-reference-{safe_kind}-{stamp}.png")
                    state = read_project_state(folder)
                    refs = state.setdefault("style_references", [])
                    refs.append({
                        "kind": kind,
                        "path": saved_path,
                        "model": result.get("model"),
                        "prompt": result.get("prompt"),
                        "pen": result.get("pen"),
                        "style": result.get("style"),
                        "paper": result.get("paper"),
                        "reference_mode": result.get("reference_mode"),
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    })
                    state["style_reference"] = {"kind": kind, "path": saved_path}
                    write_project_state(folder, state)
                result["ok"] = True
                result["project_path"] = saved_path
                return self._json(200, result)
            if self.path == "/api/workflow/generate-preset-references":
                project_id = payload.get("project_id")
                folder = safe_project_path(project_id)
                if folder is None:
                    return self._json(404, {"ok": False, "message": "项目不存在"})
                settings = payload.get("settings") or {}
                presets = payload.get("presets") or []
                overwrite = bool(payload.get("overwrite", False))
                if not isinstance(presets, list) or not presets:
                    return self._json(400, {"ok": False, "message": "没有需要生成的预设"})
                state = read_project_state(folder)
                existing = state.get("preset_references") or {}
                results = []
                for item in presets:
                    key = str((item or {}).get("key") or "").strip()
                    value = str((item or {}).get("value") or "").strip()
                    kind = str((item or {}).get("kind") or "").strip()
                    ref_key = f"{key}:{value}"
                    if not key or not value or key not in {"pen", "style", "paper"}:
                        results.append({"key": key, "value": value, "kind": kind, "status": "failed", "error": "预设参数无效"})
                        continue
                    old = existing.get(ref_key) or {}
                    if not overwrite and old.get("path") and project_file_path(folder, old.get("path")):
                        results.append({
                            "key": key, "value": value, "kind": kind or old.get("kind"),
                            "status": "skipped", "path": old.get("path"),
                            "url": scene_image_url(project_id, old.get("path")),
                            "prompt": old.get("prompt"), "model": old.get("model"),
                        })
                        continue
                    try:
                        result = generate_preset_reference(settings, key, value, kind or key)
                        saved_path = save_preset_reference(folder, result, key, value)
                        saved = persist_preset_reference(folder, result, saved_path)
                        results.append({
                            **saved,
                            "status": "ready",
                            "url": scene_image_url(project_id, saved_path),
                        })
                        existing[ref_key] = saved
                    except Exception as exc:
                        # Deliberately no retry: the user can rerun this single preset manually.
                        results.append({"key": key, "value": value, "kind": kind, "status": "failed", "error": str(exc)})
                return self._json(200, {
                    "ok": True,
                    "results": results,
                    "ready": sum(1 for item in results if item.get("status") in {"ready", "skipped"}),
                    "failed": sum(1 for item in results if item.get("status") == "failed"),
                })
            if self.path == "/api/assets/visual-presets/generate":
                key = str(payload.get("key") or "").strip()
                value = str(payload.get("value") or "").strip()
                kind = str(payload.get("kind") or "").strip()
                name = str(payload.get("name") or value).strip()
                if key not in {"pen", "penObject", "style", "paper", "scene"} or not value:
                    return self._json(400, {"ok": False, "message": "资产参数无效"})
                asset_id = str(payload.get("asset_id") or f"{key}:{preset_slug(name)}")
                overwrite = bool(payload.get("overwrite", False))
                assets = read_visual_assets()
                old = assets.get(asset_id)
                if old and not overwrite and visual_asset_file(old.get("path")):
                    item = dict(old)
                    item["url"] = f"/api/assets/file?path={quote(str(item['path']))}"
                    item["status"] = "skipped"
                    return self._json(200, {"ok": True, "asset": item})
                settings = dict(payload.get("settings") or {})
                if payload.get("prompt"):
                    settings["stylePrompt"] = str(payload["prompt"])
                result = generate_preset_reference(settings, key, value, kind or "画面参考")
                match = re.match(r"data:image/[^;]+;base64,(.+)", result.get("data_url", ""), re.S)
                if not match:
                    return self._json(400, {"ok": False, "message": "生成结果没有图片数据"})
                filename = f"{preset_slug(asset_id)}-demo.png"
                target = visual_asset_root() / filename
                target.write_bytes(base64.b64decode(match.group(1)))
                item = {
                    "asset_id": asset_id, "key": key, "value": value, "name": name,
                    "kind": kind or "画面参考", "path": filename, "demo_path": filename,
                    "prompt": result.get("prompt", ""), "model": result.get("model"),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source": "ai",
                }
                if key in {"pen", "penObject"}:
                    production_prompt = preset_production_prompt(key, value, kind or "画笔", settings)
                    production_result = generate_preset_reference(
                        {**settings, "stylePrompt": production_prompt},
                        key, value, kind or "画笔", transparent=True,
                    )
                    production_match = re.match(r"data:image/[^;]+;base64,(.+)", production_result.get("data_url", ""), re.S)
                    if not production_match:
                        return self._json(400, {"ok": False, "message": "生产笔素材没有图片数据"})
                    production_filename = f"{preset_slug(asset_id)}-production.png"
                    (visual_asset_root() / production_filename).write_bytes(base64.b64decode(production_match.group(1)))
                    item["production_path"] = production_filename
                    item["production_prompt"] = production_result.get("prompt", production_prompt)
                    item["production_transparent"] = True
                assets[asset_id] = item
                write_visual_assets(assets)
                item["url"] = f"/api/assets/file?path={quote(filename)}"
                item["status"] = "ready"
                return self._json(200, {"ok": True, "asset": item})
            if self.path == "/api/assets/visual-presets/save-custom":
                name = str(payload.get("name") or "").strip()
                data_url = str(payload.get("data_url") or "")
                if not name or not data_url.startswith("data:image/"):
                    return self._json(400, {"ok": False, "message": "自定义资产名称或图片无效"})
                match = re.match(r"data:image/[^;]+;base64,(.+)", data_url, re.S)
                if not match:
                    return self._json(400, {"ok": False, "message": "图片数据格式无效"})
                key = str(payload.get("key") or "scene")
                raw_image = base64.b64decode(match.group(1))
                if key in {"pen", "penObject"}:
                    # PNG color type 4/6 carries an alpha channel; reject opaque uploads for production overlays.
                    if not raw_image.startswith(b"\x89PNG\r\n\x1a\n") or len(raw_image) < 26 or raw_image[25] not in (4, 6):
                        return self._json(400, {"ok": False, "message": "轨迹覆盖层必须上传带 Alpha 透明通道的 PNG，当前图片没有透明底"})
                asset_id = str(payload.get("asset_id") or f"custom:{preset_slug(name)}")
                filename = f"{preset_slug(asset_id)}.png"
                (visual_asset_root() / filename).write_bytes(raw_image)
                assets = read_visual_assets()
                item = {
                    "asset_id": asset_id, "key": key, "value": name, "name": name,
                    "kind": str(payload.get("kind") or "自定义画面参考"),
                    "path": filename, "prompt": str(payload.get("prompt") or ""),
                    "model": payload.get("model"), "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source": "custom",
                }
                if key in {"pen", "penObject"}:
                    item["production_path"] = filename
                    item["production_transparent"] = True
                assets[asset_id] = item
                write_visual_assets(assets)
                item["url"] = f"/api/assets/file?path={quote(filename)}"
                return self._json(200, {"ok": True, "asset": item})
            if self.path == "/api/workflow/generate-boards/control":
                job_id = str(payload.get("job_id") or "")
                command = str(payload.get("command") or "")
                with _BOARD_JOBS_LOCK:
                    job = _BOARD_JOBS.get(job_id)
                    if not job:
                        return self._json(404, {"ok": False, "message": "分镜任务不存在"})
                    if command == "pause": job["paused"] = True; job["status"] = "paused"
                    elif command == "resume": job["paused"] = False; job["status"] = "running"
                    elif command == "cancel": job["cancelled"] = True; job["paused"] = False; job["status"] = "cancelling"
                    else: return self._json(400, {"ok": False, "message": "不支持的控制命令"})
                    return self._json(200, _board_job_snapshot(job))
            if self.path == "/api/workflow/generate-covers":
                project_id = payload.get("project_id"); folder = safe_project_path(project_id)
                if folder is None: return self._json(404, {"ok": False, "message": "项目不存在"})
                settings = {**(payload.get("settings") or {}), "project_id": project_id}
                covers = generate_cover_images(folder, settings)
                state = read_project_state(folder); state["covers"] = covers; write_project_state(folder, state)
                return self._json(200, {"ok": True, "covers": covers})
            if self.path == "/api/workflow/generate-boards":
                project_id = payload.get("project_id")
                folder = safe_project_path(project_id)
                if folder is None:
                    return self._json(404, {"ok": False, "message": "项目不存在"})
                state = read_project_state(folder)
                scenes = payload.get("scenes") or state.get("scenes") or []
                if not scenes:
                    return self._json(400, {"ok": False, "message": "没有可生成的分镜"})
                settings = payload.get("settings") or state.get("split_settings") or {}
                settings = {**settings, **(payload.get("visual_settings") or {})}
                selected_ids = payload.get("scene_ids")
                selected = None if selected_ids is None else {str(item) for item in selected_ids}
                total = sum(
                    1 for item in scenes
                    if selected is None or str((item or {}).get("id") or (item or {}).get("scene_id") or "") in selected
                )
                job_id = uuid.uuid4().hex
                job = {
                    "job_id": job_id,
                    "project_id": project_id,
                    "folder": folder,
                    "status": "running",
                    "completed": 0,
                    "total": total,
                    "current_scene_id": None,
                    "scenes": deepcopy(scenes),
                    "source_scenes": deepcopy(scenes),
                    "settings": settings,
                    "selected_ids": selected_ids,
                    "results": [],
                    "error": "",
                    "paused": False,
                    "cancelled": False,
                }
                with _BOARD_JOBS_LOCK:
                    _BOARD_JOBS[job_id] = job
                threading.Thread(target=_run_board_job, args=(job_id,), daemon=True).start()
                return self._json(202, {"ok": True, "job_id": job_id, "status": "running", "total": total})
            if self.path == "/api/workflow/generate-animation":
                project_id = payload.get("project_id")
                folder = safe_project_path(project_id)
                if folder is None:
                    return self._json(404, {"ok": False, "message": "项目不存在"})
                state = read_project_state(folder)
                saved_scenes = state.get("scenes") or []
                submitted_scenes = payload.get("scenes") or saved_scenes
                saved_by_id = {str(item.get("id") or item.get("scene_id") or ""): item for item in saved_scenes}
                scenes = []
                for item in submitted_scenes:
                    scene = dict(item or {})
                    saved = saved_by_id.get(str(scene.get("id") or scene.get("scene_id") or ""), {})
                    if saved.get("image_path"):
                        scene["image_path"] = saved["image_path"]
                        scene["image_status"] = saved.get("image_status") or "ready"
                    scenes.append(scene)
                scenes = recover_scene_board_assets(folder, scenes)
                if not scenes:
                    return self._json(400, {"ok": False, "message": "没有可渲染的分镜"})
                settings = {**(state.get("split_settings") or {}), **(payload.get("settings") or {})}
                selected_ids = payload.get("scene_ids")
                selected = None if selected_ids is None else {str(item) for item in selected_ids}
                total = sum(
                    1 for item in scenes
                    if selected is None or str((item or {}).get("id") or (item or {}).get("scene_id") or "") in selected
                )
                job_id = uuid.uuid4().hex
                job = {
                    "job_id": job_id,
                    "project_id": project_id,
                    "folder": folder,
                    "status": "running",
                    "completed": 0,
                    "total": total,
                    "current_scene_id": None,
                    "started_at": time.time(),
                    "scenes": deepcopy(scenes),
                    "source_scenes": deepcopy(scenes),
                    "settings": settings,
                    "selected_ids": selected_ids,
                    "results": [],
                    "error": "",
                }
                with _ANIMATION_JOBS_LOCK:
                    _ANIMATION_JOBS[job_id] = job
                threading.Thread(target=_run_animation_job, args=(job_id,), daemon=True).start()
                return self._json(202, {"ok": True, "job_id": job_id, "status": "running", "total": total})
            if self.path == "/api/workflow/align-audio":
                project_id = payload.get("project_id")
                folder = safe_project_path(project_id)
                if folder is None:
                    return self._json(404, {"ok": False, "message": "项目不存在"})
                state = read_project_state(folder)
                audio_info = state.get("inputs", {}).get("audio", {})
                audio_path = Path(payload.get("audio_path") or audio_info.get("path", "")).resolve()
                if folder.resolve() not in audio_path.parents or not audio_path.is_file():
                    return self._json(400, {"ok": False, "message": "未找到音频文件"})
                duration = ffprobe_duration(audio_path)
                srt_info = state.get("inputs", {}).get("srt", {}) or {}
                srt_path = Path(str(srt_info.get("path") or ""))
                if not srt_path.is_file():
                    return self._json(400, {"ok": False, "message": "请先导入 SRT 文件"})
                cues = parse_srt_text(srt_path.read_text(encoding="utf-8-sig", errors="replace"))
                if not cues:
                    return self._json(400, {"ok": False, "message": "原始 SRT 中没有有效字幕时间段"})
                origin = min(cue["start_ms"] for cue in cues)
                finish = max(cue["end_ms"] for cue in cues)
                target_ms = finish - origin
                diff_ms = None if duration is None else round(duration * 1000 - target_ms)
                state["audio_check"] = {
                    "source_path": str(audio_path.relative_to(folder)),
                    "source_duration_ms": round(duration * 1000) if duration is not None else None,
                    "target_duration_ms": target_ms,
                    "difference_ms": diff_ms,
                    "status": "ready" if duration is not None else "unverified",
                    "mode": "srt_forward_fill",
                    "gaps_filled_by_previous_scene": True,
                }
                write_project_state(folder, state)
                return self._json(200, {
                    "ok": duration is not None,
                    "audio": state["audio_check"],
                    "message": "已采用 SRT 向后对齐：音轨保持完整，分镜延长覆盖字幕间空档，最后一幕延长到音频结束。",
                })
            if self.path == "/api/workflow/render-static":
                project_id = payload.get("project_id")
                folder = safe_project_path(project_id)
                if folder is None:
                    return self._json(404, {"ok": False, "message": "项目不存在"})
                state = read_project_state(folder)
                scenes = payload.get("scenes") or state.get("scenes") or []
                result = render_static_video(folder, {
                    **payload,
                    "scenes": scenes,
                    "ratio": payload.get("ratio") or state.get("split_settings", {}).get("ratio", "16:9"),
                    "fps": payload.get("fps") or state.get("split_settings", {}).get("fps", 30),
                    "render_mode": "静态画面直合成",
                })
                if result.get("ok"):
                    update_project({"id": project_id, "stage": "export"})
                return self._json(200 if result.get("ok") else 400, result)
            if self.path == "/api/workflow/render-animation":
                project_id = payload.get("project_id")
                folder = safe_project_path(project_id)
                if folder is None:
                    return self._json(404, {"ok": False, "message": "项目不存在"})
                result = render_animation_video(folder, payload)
                if result.get("ok"):
                    update_project({"id": project_id, "stage": "export"})
                return self._json(200 if result.get("ok") else 400, result)
            if self.path == "/api/projects/open":
                folder = project_root() if payload.get("root") is True else safe_project_path(payload.get("id"))
                if folder is None:
                    return self._json(404, {"ok": False, "message": "项目文件夹不存在"})
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    relative = str(payload.get("path") or "").replace("\\", "/").strip()
                    if relative:
                        target = project_file_path(folder, relative)
                        if target is None:
                            return self._json(404, {"ok": False, "message": "项目文件不存在"})
                        folder = target.parent
                    if sys.platform == "win32":
                        os.startfile(str(folder))
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", str(folder)])
                    else:
                        subprocess.Popen(["xdg-open", str(folder)])
                    return self._json(200, {"ok": True, "message": "已在文件管理器中打开项目文件夹"})
                except OSError as exc:
                    return self._json(500, {"ok": False, "message": str(exc), "path": str(folder)})
            self._json(404, {"error": "not found"})
        except Exception as exc:
            endpoint = self.path.split("?", 1)[0]
            stage = {
                "/api/workflow/import": "文件导入",
                "/api/workflow/parse": "字幕理解与拆幕",
                "/api/workflow/generate-boards": "静态分镜生成",
                "/api/workflow/generate-animation": "单幕动画渲染",
                "/api/workflow/render-static": "FFmpeg 静态合成",
                "/api/workflow/render-animation": "FFmpeg 动画合成",
                "/api/workflow/generate-style-reference": "视觉参考图生成",
                "/api/workflow/generate-preset-references": "视觉预设生成",
            }.get(endpoint, "本地工作流服务")
            traceback.print_exc()
            self._json(500, {
                "ok": False,
                "stage": stage,
                "code": "WORKFLOW_UNHANDLED_ERROR",
                "message": f"{stage}遇到未处理错误：{type(exc).__name__}: {exc}",
                "details": f"接口：{endpoint}\n异常类型：{type(exc).__name__}\n项目：{payload.get('project_id') or '未指定'}",
                "hint": "查看启动本地工作流服务的终端输出，可定位完整服务端堆栈；API Key 不会包含在诊断信息中。",
            })


if __name__ == "__main__":
    port = int(os.environ.get("WORKFLOW_PORT", "8766"))
    print(f"Workflow app: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
