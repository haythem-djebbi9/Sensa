"""Export de la transcription en sous-titres (WebVTT, SRT) ou en texte brut."""


def _cues(audio):
    segments = [seg for seg in (audio.segments or []) if (seg.get("text") or "").strip()]
    if not segments and audio.transcription:
        return [(0.0, float(audio.duration or 5), audio.transcription.strip())]
    cues = []
    for index, seg in enumerate(segments):
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or 0)
        if end <= start:
            next_start = float(segments[index + 1].get("start") or 0) if index + 1 < len(segments) else start + 4
            end = max(next_start, start + 1)
        cues.append((start, end, seg["text"].strip()))
    return cues


def _timestamp(seconds, separator):
    milliseconds = int(round(seconds * 1000))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def to_vtt(audio):
    lines = ["WEBVTT", ""]
    for start, end, text in _cues(audio):
        lines += [f"{_timestamp(start, '.')} --> {_timestamp(end, '.')}", text, ""]
    return "\n".join(lines)


def to_srt(audio):
    lines = []
    for number, (start, end, text) in enumerate(_cues(audio), start=1):
        lines += [str(number), f"{_timestamp(start, ',')} --> {_timestamp(end, ',')}", text, ""]
    return "\n".join(lines)


def to_txt(audio):
    return (audio.transcription or "").strip() + "\n"
