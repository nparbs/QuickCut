"""Turn a list of clips plus export settings into ffmpeg command lines."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .ffmpeg import ENCODERS, Encoder
from .models import Clip, ExportSettings

# x264 CRF values for each quality preset.
QUALITY_PRESETS = {"high": 18, "medium": 23, "low": 28, "smallest": 33}
# CRF scales differ per codec; these offsets give roughly the look of the x264 value.
QUALITY_OFFSET = {"h264": 0, "hevc": 3, "av1": 9}

SPEED = {
    "x264": {"quality": "slow", "balanced": "medium", "fast": "veryfast"},
    "x265": {"quality": "slow", "balanced": "medium", "fast": "veryfast"},
    "svtav1": {"quality": "4", "balanced": "6", "fast": "9"},
    "nvenc": {"quality": "p7", "balanced": "p5", "fast": "p2"},
    "amf": {"quality": "quality", "balanced": "balanced", "fast": "speed"},
    "qsv": {"quality": "slow", "balanced": "medium", "fast": "veryfast"},
}

MP4_LIKE = {".mp4", ".mov", ".m4v"}
TWO_PASS_FAMILIES = {"x264", "x265"}
PASSLOG = "quickcut2pass"   # relative to the job's working directory
SAMPLE_RATE = 48000


@dataclass
class RateControl:
    kind: str    # quality | bitrate
    value: int   # CRF/CQ value, or kbps


@dataclass
class ExportPlan:
    passes: list[list[str]]
    duration: float
    width: int
    height: int
    fps: str
    output: str
    video_kbps: int | None
    audio_kbps: int
    target_bytes: int | None = None   # size mode: the file must not exceed this

    def with_bitrate_scaled(self, factor: float) -> "ExportPlan":
        """Same plan with every video bitrate scaled, used to retry an export that came out too big."""
        passes = []
        for cmd in self.passes:
            cmd = list(cmd)
            for i, arg in enumerate(cmd[:-1]):
                if arg in ("-b:v", "-maxrate", "-bufsize") and cmd[i + 1].endswith("k"):
                    cmd[i + 1] = f"{max(30, int(int(cmd[i + 1][:-1]) * factor))}k"
            passes.append(cmd)
        kbps = max(30, int(self.video_kbps * factor)) if self.video_kbps else None
        return replace(self, passes=passes, video_kbps=kbps)


def retry_plan_if_too_big(plan: ExportPlan, actual_bytes: int) -> ExportPlan | None:
    if not plan.target_bytes or actual_bytes <= plan.target_bytes:
        return None
    return plan.with_bitrate_scaled(plan.target_bytes / actual_bytes * 0.95)


def _even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def output_geometry(clips: list[Clip], s: ExportSettings) -> tuple[int, int, str]:
    """Width, height and frame rate of the export; clips that differ are fitted to it."""
    first = clips[0].info
    if s.height:
        h = _even(s.height)
        w = _even(s.height * first.width / first.height)
    else:
        w, h = _even(first.width), _even(first.height)
    return w, h, s.fps or first.fps


def effective_crossfade(clips: list[Clip], s: ExportSettings) -> float:
    if len(clips) < 2 or s.crossfade <= 0:
        return 0.0
    # A fade can't be longer than half of the shortest clip it touches.
    return round(min(s.crossfade, min(c.length for c in clips) / 2), 3)


def output_duration(clips: list[Clip], s: ExportSettings) -> float:
    return sum(c.length for c in clips) - effective_crossfade(clips, s) * (len(clips) - 1)


def uses_two_pass(s: ExportSettings) -> bool:
    return s.mode == "size" and s.two_pass and ENCODERS[s.encoder].family in TWO_PASS_FAMILIES


def compute_bitrates(clips: list[Clip], s: ExportSettings) -> tuple[int | None, int]:
    """(video kbps or None for quality-based modes, audio kbps)."""
    audio_k = 0 if s.mute else s.audio_bitrate_k
    if s.mode == "size":
        duration = max(output_duration(clips, s), 0.1)
        # Leave headroom for container overhead; single-pass encoders overshoot more.
        margin = 0.96 if uses_two_pass(s) else 0.92
        total_k = s.target_size_mb * 1024 * 1024 * 8 / 1000 / duration * margin
        if audio_k and total_k < audio_k * 4:
            audio_k = min(audio_k, 64)
        return max(50, int(total_k - audio_k)), audio_k
    if s.mode == "manual" and s.manual_rate == "bitrate":
        return s.manual_bitrate_k, audio_k
    return None, audio_k


def rate_control(s: ExportSettings, enc: Encoder, video_kbps: int | None) -> RateControl:
    if video_kbps is not None:
        return RateControl("bitrate", video_kbps)
    if s.mode == "manual":
        return RateControl("quality", s.manual_quality)
    return RateControl("quality", QUALITY_PRESETS[s.preset] + QUALITY_OFFSET[enc.codec])


def encoder_args(enc: Encoder, rc: RateControl, speed: str, pass_num: int | None = None) -> list[str]:
    f = enc.family
    args = ["-c:v", enc.name, "-preset", SPEED[f][speed]] if f != "amf" else \
           ["-c:v", enc.name, "-quality", SPEED[f][speed]]
    q = k = rc.value
    bitrate = ["-b:v", f"{k}k"]
    capped = bitrate + ["-maxrate", f"{int(k * 1.5)}k", "-bufsize", f"{k * 2}k"]

    if f in ("x264", "x265"):
        args += ["-crf", str(q)] if rc.kind == "quality" else bitrate
        if f == "x264" and pass_num:
            args += ["-pass", str(pass_num), "-passlogfile", PASSLOG]
        if f == "x265":
            params = "log-level=error"
            if pass_num:
                params += f":pass={pass_num}:stats={PASSLOG}.log"
            args += ["-x265-params", params]
    elif f == "svtav1":
        args += ["-crf", str(min(q, 63))] if rc.kind == "quality" else                 bitrate + ["-svtav1-params", "overshoot-pct=0"]
    elif f == "nvenc":
        args += ["-tune", "hq", "-rc", "vbr"]
        if rc.kind == "quality":
            args += ["-cq", str(min(q, 51)), "-b:v", "0"]
        else:
            args += ["-multipass", "fullres"] + capped
    elif f == "amf":
        if rc.kind == "quality":
            # AV1 on AMF uses a 0-255 QP scale.
            qp = str(min(q * 4, 255)) if enc.codec == "av1" else str(min(q, 51))
            args += ["-rc", "cqp", "-qp_i", qp, "-qp_p", qp]
            if enc.codec != "av1":
                args += ["-qp_b", qp]
        else:
            args += ["-rc", "vbr_peak"] + capped
    elif f == "qsv":
        args += ["-global_quality", str(min(q, 51))] if rc.kind == "quality" else capped
    return args


def build_filtergraph(clips: list[Clip], w: int, h: int, fps: str, crossfade: float,
                      audio: bool, volume: float = 1.0) -> str:
    """One filter_complex that trims are already applied to (via -ss/-t), producing [vout]/[aout]."""
    parts = []
    for i, c in enumerate(clips):
        length = f"{c.length:.3f}"
        # Fit into the output frame, then pad/trim so every clip is exactly its nominal length;
        # that keeps audio and video in sync and the crossfade offsets correct.
        parts.append(
            f"[{i}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p,"
            f"setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=1,trim=duration={length}[v{i}]"
        )
        if audio:
            fmt = "aformat=sample_fmts=fltp:channel_layouts=stereo"
            if c.info.has_audio:
                parts.append(f"[{i}:a:0]aresample={SAMPLE_RATE},{fmt},asetpts=PTS-STARTPTS,"
                             f"apad,atrim=duration={length}[a{i}]")
            else:
                parts.append(f"anullsrc=r={SAMPLE_RATE}:cl=stereo,atrim=duration={length},{fmt}[a{i}]")

    n = len(clips)
    v, a = "v0", "a0"
    if n > 1 and crossfade > 0:
        offset = 0.0
        for i in range(1, n):
            offset += clips[i - 1].length - crossfade
            parts.append(f"[{v}][v{i}]xfade=transition=fade:duration={crossfade:.3f}"
                         f":offset={offset:.3f}[vx{i}]")
            v = f"vx{i}"
            if audio:
                parts.append(f"[{a}][a{i}]acrossfade=d={crossfade:.3f}[ax{i}]")
                a = f"ax{i}"
    elif n > 1:
        inputs = "".join(f"[v{i}]" + (f"[a{i}]" if audio else "") for i in range(n))
        parts.append(f"{inputs}concat=n={n}:v=1:a={int(audio)}[vc]" + ("[ac]" if audio else ""))
        v, a = "vc", "ac"

    # xfade drops the frame rate and negotiates 4:4:4 output; pin both so encoders get plain 4:2:0.
    parts.append(f"[{v}]fps={fps},format=yuv420p[vout]")
    if audio:
        parts.append(f"[{a}]volume={volume:.2f}[aout]" if volume != 1.0 else f"[{a}]anull[aout]")
    return ";".join(parts)


def build_plan(ffmpeg: str, clips: list[Clip], s: ExportSettings) -> ExportPlan:
    if not clips:
        raise ValueError("Add at least one clip")
    if any(c.length < 0.05 for c in clips):
        raise ValueError("Every clip needs to be longer than 0.05 seconds")
    if not s.output:
        raise ValueError("No output file chosen")

    enc = ENCODERS[s.encoder]
    w, h, fps = output_geometry(clips, s)
    crossfade = effective_crossfade(clips, s)
    audio = not s.mute
    video_k, audio_k = compute_bitrates(clips, s)
    rc = rate_control(s, enc, video_k)
    two_pass = uses_two_pass(s)
    ext = Path(s.output).suffix.lower()

    head = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-progress", "pipe:1", "-nostats"]
    inputs: list[str] = []
    for c in clips:
        inputs += ["-ss", f"{c.start:.3f}", "-t", f"{c.length:.3f}", "-i", c.info.path]
    tag = ["-tag:v", "hvc1"] if enc.codec == "hevc" and ext in MP4_LIKE else []

    passes = []
    if two_pass:
        graph = build_filtergraph(clips, w, h, fps, crossfade, audio=False)
        passes.append(head + inputs + ["-filter_complex", graph, "-map", "[vout]"]
                      + encoder_args(enc, rc, s.speed, 1) + ["-an", "-f", "null", "-"])

    graph = build_filtergraph(clips, w, h, fps, crossfade, audio, s.volume)
    cmd = head + inputs + ["-filter_complex", graph, "-map", "[vout]"]
    if audio:
        cmd += ["-map", "[aout]"]
    cmd += encoder_args(enc, rc, s.speed, 2 if two_pass else None) + tag
    if audio:
        cmd += ["-c:a", "aac", "-b:a", f"{audio_k}k"]
    if ext in MP4_LIKE:
        cmd += ["-movflags", "+faststart"]
    cmd.append(s.output)
    passes.append(cmd)

    target = int(s.target_size_mb * 1024 * 1024) if s.mode == "size" else None
    return ExportPlan(passes, output_duration(clips, s), w, h, fps, s.output, video_k, audio_k, target)
