"""Turn a list of clips plus export settings into ffmpeg command lines."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from .ffmpeg import ENCODERS, Encoder
from .models import Clip, ExportSettings

# x264 CRF values for each quality preset.
QUALITY_PRESETS = {"high": 18, "medium": 23, "low": 28, "smallest": 33}
# CRF scales differ per codec; these offsets give roughly the look of the x264 value.
QUALITY_OFFSET = {"h264": 0, "hevc": 3, "av1": 9, "vp9": 8}
GIF_COLORS = {"high": 256, "medium": 192, "low": 128, "smallest": 64}
WEBP_QUALITY = {"high": 85, "medium": 70, "low": 55, "smallest": 40}

SPEED = {
    "x264": {"quality": "slow", "balanced": "medium", "fast": "veryfast"},
    "x265": {"quality": "slow", "balanced": "medium", "fast": "veryfast"},
    "svtav1": {"quality": "4", "balanced": "6", "fast": "9"},
    "vpx": {"quality": "1", "balanced": "3", "fast": "5"},
    "nvenc": {"quality": "p7", "balanced": "p5", "fast": "p2"},
    "amf": {"quality": "quality", "balanced": "balanced", "fast": "speed"},
    "qsv": {"quality": "slow", "balanced": "medium", "fast": "veryfast"},
}

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm"}
AUDIO_CODECS = {".mp3": "libmp3lame", ".m4a": "aac", ".wav": "pcm_s16le"}
MP4_LIKE = {".mp4", ".mov", ".m4v", ".m4a"}
# Codecs each container can hold; anything not listed accepts every codec.
CONTAINER_CODECS = {".webm": {"vp9", "av1"}, ".mov": {"h264", "hevc"}}
TWO_PASS_FAMILIES = {"x264", "x265", "vpx"}
PASSLOG = "quickcut2pass"   # relative to the job's working directory
SAMPLE_RATE = 48000
ASPECTS = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:5": 4 / 5, "4:3": 4 / 3, "21:9": 21 / 9}
RESOLUTION_STEPS = [2160, 1440, 1080, 720, 540, 480, 360, 240]
# Below this many bits per pixel the codec turns blocky; used to auto-lower the resolution.
MIN_BITS_PER_PIXEL = {"h264": 0.045, "hevc": 0.03, "vp9": 0.03, "av1": 0.025}
GIF_MAX_FPS = 15
TRANSITIONS = ["fade", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight", "wipeup",
               "wipedown", "slideleft", "slideright", "slideup", "slidedown", "smoothleft",
               "smoothright", "circleopen", "circleclose", "radial", "zoomin", "pixelize",
               "hblur", "distance"]
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
AUDIO_FORMAT = "aformat=sample_fmts=fltp:channel_layouts=stereo"


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
    target_bytes: int | None = None     # size mode: the file must not exceed this
    pass_durations: list[float] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)    # written to the work dir first
    fallback: ExportPlan | None = None  # same export without hardware decoding
    notes: list[str] = field(default_factory=list)

    def with_bitrate_scaled(self, factor: float) -> ExportPlan:
        """Same plan with every video bitrate scaled, used to retry an export that came out too big."""
        passes = []
        for cmd in self.passes:
            cmd = list(cmd)
            for i, arg in enumerate(cmd[:-1]):
                if arg in ("-b:v", "-maxrate", "-bufsize") and cmd[i + 1].endswith("k"):
                    cmd[i + 1] = f"{max(30, int(int(cmd[i + 1][:-1]) * factor))}k"
            passes.append(cmd)
        kbps = max(30, int(self.video_kbps * factor)) if self.video_kbps else None
        fallback = self.fallback.with_bitrate_scaled(factor) if self.fallback else None
        return replace(self, passes=passes, video_kbps=kbps, fallback=fallback)


def retry_plan_if_too_big(plan: ExportPlan, actual_bytes: int) -> ExportPlan | None:
    if not plan.target_bytes or actual_bytes <= plan.target_bytes:
        return None
    return plan.with_bitrate_scaled(plan.target_bytes / actual_bytes * 0.95)


# -- what kind of file ----------------------------------------------------------------------------

def output_kind(path: str) -> str:
    """video | gif | webp | audio"""
    ext = Path(path).suffix.lower()
    if ext == ".gif":
        return "gif"
    if ext == ".webp":
        return "webp"
    if ext in AUDIO_CODECS:
        return "audio"
    return "video"


def has_audio_out(s: ExportSettings, kind: str) -> bool:
    return kind in ("video", "audio") and (not s.mute or bool(s.music))


def codec_allowed(ext: str, codec: str) -> bool:
    return codec in CONTAINER_CODECS.get(ext.lower(), {codec})


# -- geometry and timing --------------------------------------------------------------------------

def _even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def _fps_value(rate: str) -> float:
    if "/" in rate:
        num, den = rate.split("/")
        return float(num) / float(den)
    return float(rate)


def output_frame(clips: list[Clip], s: ExportSettings, short: int | None = None) -> tuple[int, int]:
    """Output width/height. "1080p" means the short side, so 9:16 at 1080p is 1080x1920."""
    bw, bh = clips[0].display_size
    ar = ASPECTS.get(s.aspect or "", bw / bh)
    if short is None:
        if s.height:
            short = s.height
        elif s.aspect and s.fit == "crop":
            # Biggest frame of that shape that fits inside the source, so nothing gets upscaled.
            iw, ih = (bw, bw / ar) if ar >= bw / bh else (bh * ar, bh)
            short = min(iw, ih)
        else:
            short = min(bw, bh)
    return (_even(short * ar), _even(short)) if ar >= 1 else (_even(short), _even(short / ar))


def output_fps(clips: list[Clip], s: ExportSettings) -> str:
    if s.fps:
        return s.fps
    rate = clips[0].info.fps
    if output_kind(s.output) in ("gif", "webp") and _fps_value(rate) > GIF_MAX_FPS:
        return str(GIF_MAX_FPS)
    return rate


def output_geometry(clips: list[Clip], s: ExportSettings) -> tuple[int, int, str]:
    w, h = output_frame(clips, s)
    return w, h, output_fps(clips, s)


def effective_crossfade(clips: list[Clip], s: ExportSettings) -> float:
    if len(clips) < 2 or s.crossfade <= 0:
        return 0.0
    # A transition can't be longer than half of the shortest clip it touches.
    return round(min(s.crossfade, min(c.out_length for c in clips) / 2), 3)


def output_duration(clips: list[Clip], s: ExportSettings) -> float:
    if s.mode == "copy":
        return sum(c.length for c in clips)
    return sum(c.out_length for c in clips) - effective_crossfade(clips, s) * (len(clips) - 1)


def uses_two_pass(s: ExportSettings) -> bool:
    return (s.mode == "size" and s.two_pass and output_kind(s.output) == "video"
            and ENCODERS[s.encoder].family in TWO_PASS_FAMILIES)


def compute_bitrates(clips: list[Clip], s: ExportSettings) -> tuple[int | None, int]:
    """(video kbps or None for quality-based modes, audio kbps)."""
    kind = output_kind(s.output) if s.output else "video"
    audio_k = s.audio_bitrate_k if has_audio_out(s, kind) else 0
    if kind in ("gif", "webp") or s.mode == "copy":
        return None, 0
    if kind == "audio":
        return None, audio_k
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


def bits_per_pixel(video_kbps: int, w: int, h: int, fps: str) -> float:
    return video_kbps * 1000 / (w * h * _fps_value(fps))


def auto_short_side(clips: list[Clip], s: ExportSettings, video_kbps: int, fps: str) -> int | None:
    """In size mode, the short side to drop to so the bitrate stays watchable, or None to keep it."""
    w, h = output_frame(clips, s)
    current = min(w, h)
    minimum = MIN_BITS_PER_PIXEL.get(ENCODERS[s.encoder].codec, 0.04)
    if bits_per_pixel(video_kbps, w, h, fps) >= minimum:
        return None
    best = None
    for step in RESOLUTION_STEPS:
        if step >= current:
            continue
        best = step
        if bits_per_pixel(video_kbps, *output_frame(clips, s, step), fps) >= minimum:
            break
    return best


# -- encoders -------------------------------------------------------------------------------------

def rate_control(s: ExportSettings, enc: Encoder, video_kbps: int | None) -> RateControl:
    if video_kbps is not None:
        return RateControl("bitrate", video_kbps)
    if s.mode == "manual":
        return RateControl("quality", s.manual_quality)
    return RateControl("quality", QUALITY_PRESETS[s.preset] + QUALITY_OFFSET[enc.codec])


def encoder_args(enc: Encoder, rc: RateControl, speed: str, pass_num: int | None = None) -> list[str]:
    f = enc.family
    q = k = rc.value
    bitrate = ["-b:v", f"{k}k"]
    capped = bitrate + ["-maxrate", f"{int(k * 1.5)}k", "-bufsize", f"{k * 2}k"]
    args = ["-c:v", enc.name]

    if f in ("x264", "x265"):
        args += ["-preset", SPEED[f][speed]]
        args += ["-crf", str(q)] if rc.kind == "quality" else bitrate
        if f == "x264" and pass_num:
            args += ["-pass", str(pass_num), "-passlogfile", PASSLOG]
        if f == "x265":
            params = "log-level=error"
            if pass_num:
                params += f":pass={pass_num}:stats={PASSLOG}.log"
            args += ["-x265-params", params]
    elif f == "svtav1":
        args += ["-preset", SPEED[f][speed]]
        args += ["-crf", str(min(q, 63))] if rc.kind == "quality" else \
                bitrate + ["-svtav1-params", "overshoot-pct=0"]
    elif f == "vpx":
        args += ["-deadline", "good", "-cpu-used", SPEED[f][speed], "-row-mt", "1"]
        args += ["-crf", str(min(q, 63)), "-b:v", "0"] if rc.kind == "quality" else bitrate
        if pass_num:
            args += ["-pass", str(pass_num), "-passlogfile", PASSLOG]
    elif f == "nvenc":
        args += ["-preset", SPEED[f][speed], "-tune", "hq", "-rc", "vbr"]
        if rc.kind == "quality":
            args += ["-cq", str(min(q, 51)), "-b:v", "0"]
        else:
            args += ["-multipass", "fullres"] + capped
    elif f == "amf":
        args += ["-quality", SPEED[f][speed]]
        if rc.kind == "quality":
            # AV1 on AMF uses a 0-255 QP scale.
            qp = str(min(q * 4, 255)) if enc.codec == "av1" else str(min(q, 51))
            args += ["-rc", "cqp", "-qp_i", qp, "-qp_p", qp]
            if enc.codec != "av1":
                args += ["-qp_b", qp]
        else:
            args += ["-rc", "vbr_peak"] + capped
    elif f == "qsv":
        args += ["-preset", SPEED[f][speed]]
        args += ["-global_quality", str(min(q, 51))] if rc.kind == "quality" else capped
    return args


def _atempo(speed: float) -> list[float]:
    # atempo only accepts 0.5-2 per instance, so chain several for bigger changes.
    factors = []
    while speed > 2:
        factors.append(2.0)
        speed /= 2
    while speed < 0.5:
        factors.append(0.5)
        speed /= 0.5
    if abs(speed - 1) > 1e-6:
        factors.append(speed)
    return factors


# -- filter graph ---------------------------------------------------------------------------------

def _clip_video(i: int, c: Clip, s: ExportSettings, w: int, h: int, fps: str) -> list[str]:
    pre = []
    if c.info.hdr and s.tonemap:
        pre.append(TONEMAP)
    if c.hflip:
        pre.append("hflip")
    if c.vflip:
        pre.append("vflip")
    pre += {90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}.get(c.rotate, [])
    pre.append("setpts=PTS-STARTPTS")
    if c.reverse:
        pre.append("reverse")
    if c.speed != 1:
        pre.append(f"setpts=PTS/{c.speed:g}")
    pre = ",".join(pre)
    # Pad/trim every clip to exactly its nominal length; that keeps audio and video in sync
    # and the transition offsets correct.
    post = (f"setsar=1,fps={fps},format=yuv420p,tpad=stop_mode=clone:stop_duration=1,"
            f"trim=duration={c.out_length:.3f}")
    if s.fit == "blur":
        bw, bh = _even(w / 4), _even(h / 4)
        return [
            f"[{i}:v:0]{pre},split[s{i}a][s{i}b]",
            f"[s{i}a]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=8,scale={w}:{h},setsar=1[bg{i}]",
            f"[s{i}b]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"setsar=1[fg{i}]",
            f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,{post}[v{i}]",
        ]
    if s.fit == "crop":
        fit = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    else:
        fit = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    return [f"[{i}:v:0]{pre},{fit},{post}[v{i}]"]


def _clip_audio(i: int, c: Clip) -> str:
    length = f"{c.out_length:.3f}"
    if not c.info.has_audio:
        return f"anullsrc=r={SAMPLE_RATE}:cl=stereo,atrim=duration={length},{AUDIO_FORMAT}[a{i}]"
    f = [f"aresample={SAMPLE_RATE}", AUDIO_FORMAT, "asetpts=PTS-STARTPTS"]
    if c.reverse:
        f.append("areverse")
    f += [f"atempo={t:g}" for t in _atempo(c.speed)]
    f += ["apad", f"atrim=duration={length}"]
    return f"[{i}:a:0]{','.join(f)}[a{i}]"


def build_filtergraph(clips: list[Clip], s: ExportSettings, w: int, h: int, fps: str,
                      video: bool = True, audio: bool = True, kind: str = "video",
                      overlay_input: int | None = None, music_input: int | None = None) -> str:
    """One filter_complex producing [vout] and/or [aout]. Trims are applied by -ss/-t on the inputs."""
    parts: list[str] = []
    n = len(clips)
    crossfade = effective_crossfade(clips, s)
    duration = output_duration(clips, s)
    clip_audio = audio and not s.mute

    for i, c in enumerate(clips):
        if video:
            parts += _clip_video(i, c, s, w, h, fps)
        if clip_audio:
            parts.append(_clip_audio(i, c))

    v, a = "v0", "a0"
    if n > 1 and crossfade > 0:
        offset = 0.0
        for i in range(1, n):
            offset += clips[i - 1].out_length - crossfade
            if video:
                parts.append(f"[{v}][v{i}]xfade=transition={s.transition}:duration={crossfade:.3f}"
                             f":offset={offset:.3f}[vx{i}]")
                v = f"vx{i}"
            if clip_audio:
                parts.append(f"[{a}][a{i}]acrossfade=d={crossfade:.3f}[ax{i}]")
                a = f"ax{i}"
    elif n > 1:
        inputs = "".join((f"[v{i}]" if video else "") + (f"[a{i}]" if clip_audio else "")
                         for i in range(n))
        outs = ("[vc]" if video else "") + ("[ac]" if clip_audio else "")
        parts.append(f"{inputs}concat=n={n}:v={int(video)}:a={int(clip_audio)}{outs}")
        v, a = "vc", "ac"

    if video:
        for k, ov in enumerate(s.overlays if overlay_input is not None else []):
            end = duration if ov.end is None else min(ov.end, duration)
            enable = (f":enable='between(t,{ov.start:.3f},{end:.3f})'"
                      if ov.start > 0 or ov.end is not None else "")
            parts.append(f"[{v}][{overlay_input + k}:v]overlay=0:0{enable}[ov{k}]")
            v = f"ov{k}"
        # fps/format again: xfade drops the frame rate and negotiates 4:4:4, which some encoders
        # reject and others encode as a much heavier stream.
        tail = [f"fps={fps}"]
        if s.fade_in > 0:
            tail.append(f"fade=t=in:st=0:d={s.fade_in:.3f}")
        if s.fade_out > 0:
            tail.append(f"fade=t=out:st={max(0.0, duration - s.fade_out):.3f}:d={s.fade_out:.3f}")
        if kind == "gif":
            colors = GIF_COLORS[s.preset]
            parts.append(f"[{v}]{','.join(tail)},split[g1][g2]")
            parts.append(f"[g1]palettegen=max_colors={colors}:stats_mode=diff[pal]")
            parts.append("[g2][pal]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle[vout]")
        else:
            tail.append("format=yuv420p")
            parts.append(f"[{v}]{','.join(tail)}[vout]")

    if audio:
        cur = a if clip_audio else None
        if cur and s.volume != 1.0:
            parts.append(f"[{cur}]volume={s.volume:.2f}[avol]")
            cur = "avol"
        if s.music and music_input is not None:
            fade = min(2.0, duration / 4)
            parts.append(f"[{music_input}:a:0]aresample={SAMPLE_RATE},{AUDIO_FORMAT},"
                         f"asetpts=PTS-STARTPTS,volume={s.music_volume:.2f},apad,"
                         f"atrim=duration={duration:.3f},"
                         f"afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}[am]")
            if cur:
                parts.append(f"[{cur}][am]amix=inputs=2:duration=first:dropout_transition=0:"
                             f"normalize=0[amx]")
                cur = "amx"
            else:
                cur = "am"
        tail = []
        if s.normalize:
            tail += ["loudnorm=I=-16:TP=-1.5:LRA=11", f"aresample={SAMPLE_RATE}"]
        if s.fade_in > 0:
            tail.append(f"afade=t=in:st=0:d={s.fade_in:.3f}")
        if s.fade_out > 0:
            tail.append(f"afade=t=out:st={max(0.0, duration - s.fade_out):.3f}:d={s.fade_out:.3f}")
        parts.append(f"[{cur}]{','.join(tail) or 'anull'}[aout]")
    return ";".join(parts)


# -- copy mode ------------------------------------------------------------------------------------

def copy_mode_problems(clips: list[Clip], s: ExportSettings) -> tuple[list[str], list[str]]:
    """(errors that block a no-re-encode export, warnings about settings it ignores)."""
    errors, warnings = [], []
    ext = Path(s.output).suffix.lower() if s.output else ".mp4"
    if output_kind(s.output or "x.mp4") != "video":
        errors.append("No re-encode only works for video formats (MP4, MKV, MOV, WebM).")
    if not codec_allowed(ext, clips[0].info.video_codec):
        errors.append(f"{clips[0].info.video_codec.upper()} video can't go in a {ext} file "
                      "without re-encoding.")
    if len(clips) > 1:
        first = clips[0].info
        if any((c.info.video_codec, c.info.width, c.info.height) !=
               (first.video_codec, first.width, first.height) for c in clips):
            errors.append("Clips have different codecs or sizes, so they can't be joined "
                          "without re-encoding.")
        if len({c.info.has_audio for c in clips}) > 1:
            errors.append("Some clips have audio and some don't, so they can't be joined "
                          "without re-encoding.")
    if any(c.has_effects for c in clips):
        warnings.append("Speed, rotation, flips and reverse are ignored.")
    if (s.height or s.fps or s.aspect or s.overlays or s.music or s.fade_in or s.fade_out
            or s.crossfade or s.normalize or s.volume != 1.0):
        warnings.append("Resolution, frame rate, framing, text, music, fades, transitions and "
                        "volume are ignored.")
    warnings.append("Cuts snap to the nearest keyframe, so they can be off by up to a few seconds.")
    return errors, warnings


def _copy_plan(head: list[str], clips: list[Clip], s: ExportSettings) -> ExportPlan:
    ext = Path(s.output).suffix.lower()
    faststart = ["-movflags", "+faststart"] if ext in MP4_LIKE else []
    audio = [] if s.mute else ["-map", "0:a:0?"]

    def cut(c: Clip) -> list[str]:
        return head + ["-ss", f"{c.start:.3f}", "-t", f"{c.length:.3f}", "-i", c.info.path,
                       "-map", "0:v:0", *audio, "-c", "copy", "-avoid_negative_ts", "make_zero"]

    w, h = clips[0].display_size
    total = sum(c.length for c in clips)
    if len(clips) == 1:
        return ExportPlan([cut(clips[0]) + faststart + [s.output]], total, w, h, clips[0].info.fps,
                          s.output, None, 0, pass_durations=[total])
    passes = [cut(c) + [f"part{i}.mkv"] for i, c in enumerate(clips)]
    listing = "".join(f"file 'part{i}.mkv'\n" for i in range(len(clips)))
    passes.append(head + ["-f", "concat", "-safe", "0", "-i", "parts.txt", "-c", "copy",
                          *faststart, s.output])
    return ExportPlan(passes, total, w, h, clips[0].info.fps, s.output, None, 0,
                      pass_durations=[c.length for c in clips] + [total],
                      files={"parts.txt": listing})


# -- plan -----------------------------------------------------------------------------------------

def build_plan(ffmpeg: str, clips: list[Clip], s: ExportSettings) -> ExportPlan:
    if not clips:
        raise ValueError("Add at least one clip")
    if any(c.length < 0.05 for c in clips):
        raise ValueError("Every clip needs to be longer than 0.05 seconds")
    if not s.output:
        raise ValueError("No output file chosen")

    kind = output_kind(s.output)
    ext = Path(s.output).suffix.lower()
    head = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-progress", "pipe:1", "-nostats"]

    if s.mode == "copy":
        errors, _ = copy_mode_problems(clips, s)
        if errors:
            raise ValueError(errors[0])
        return _copy_plan(head, clips, s)

    enc = ENCODERS[s.encoder]
    if kind == "video" and not codec_allowed(ext, enc.codec):
        raise ValueError(f"{ext} files can't hold {enc.codec.upper()} video; "
                         "pick another codec or format.")
    audio = has_audio_out(s, kind)
    video = kind != "audio"
    if kind == "audio" and not audio:
        raise ValueError("There's no audio to export: the clips' audio is removed and "
                         "there's no music.")

    w, h = output_frame(clips, s)
    fps = output_fps(clips, s)
    duration = output_duration(clips, s)
    video_k, audio_k = compute_bitrates(clips, s)
    notes = []
    if kind == "video" and video_k is not None and s.mode == "size" and s.auto_resolution:
        short = auto_short_side(clips, s, video_k, fps)
        if short:
            w, h = output_frame(clips, s, short)
            notes.append(f"Resolution lowered to {short}p so the video stays watchable at this size.")

    hw = ["-hwaccel", "auto"] if s.hw_decode else []
    inputs: list[str] = []
    for c in clips:
        inputs += hw + ["-ss", f"{c.start:.3f}", "-t", f"{c.length:.3f}", "-i", c.info.path]
    next_input = len(clips)
    overlay_input = None
    if video and s.overlays:
        overlay_input = next_input
        for ov in s.overlays:
            inputs += ["-i", ov.image]
        next_input += len(s.overlays)
    music_input = None
    if audio and s.music:
        music_input = next_input
        inputs += (["-stream_loop", "-1"] if s.music_loop else []) + \
                  ["-t", f"{duration + 1:.3f}", "-i", s.music]

    def graph(with_audio: bool) -> str:
        return build_filtergraph(clips, s, w, h, fps, video=video, audio=with_audio, kind=kind,
                                 overlay_input=overlay_input, music_input=music_input)

    passes = []
    cmd = head + inputs + ["-filter_complex", graph(audio)]
    if video:
        cmd += ["-map", "[vout]"]
    if audio:
        cmd += ["-map", "[aout]"]

    if kind == "gif":
        cmd += ["-loop", "0", s.output]
    elif kind == "webp":
        cmd += ["-c:v", "libwebp_anim", "-lossless", "0", "-q:v", str(WEBP_QUALITY[s.preset]),
                "-compression_level", "4", "-loop", "0", s.output]
    elif kind == "audio":
        cmd += ["-c:a", AUDIO_CODECS[ext]]
        if ext != ".wav":
            cmd += ["-b:a", f"{audio_k}k"]
        cmd += (["-movflags", "+faststart"] if ext in MP4_LIKE else []) + [s.output]
    else:
        rc = rate_control(s, enc, video_k)
        two_pass = uses_two_pass(s)
        if two_pass:
            passes.append(head + inputs + ["-filter_complex", graph(False), "-map", "[vout]"]
                          + encoder_args(enc, rc, s.speed, 1) + ["-an", "-f", "null", "-"])
        cmd += encoder_args(enc, rc, s.speed, 2 if two_pass else None)
        if enc.codec == "hevc" and ext in MP4_LIKE:
            cmd += ["-tag:v", "hvc1"]
        if audio:
            cmd += ["-c:a", "libopus" if ext == ".webm" else "aac", "-b:a", f"{audio_k}k"]
        if ext in MP4_LIKE:
            cmd += ["-movflags", "+faststart"]
        cmd.append(s.output)
    passes.append(cmd)

    target = int(s.target_size_mb * 1024 * 1024) if s.mode == "size" and kind == "video" else None
    fallback = build_plan(ffmpeg, clips, replace(s, hw_decode=False)) if s.hw_decode else None
    return ExportPlan(passes, duration, w, h, fps, s.output, video_k, audio_k, target,
                      pass_durations=[duration] * len(passes), fallback=fallback, notes=notes)
