"""Utility functions for processing TTS audio."""

import os
import re

import ffmpeg


def get_max_volume(filepath: str) -> float:
    """Get the maximum volume of an audio file using ffmpeg volumedetect.

    Inverse of return value used with ffmpeg .volume(volume="x.xdB") filter to increase file to 0db max peak.

    When used with "volume" guarantees that the entire file loudness is increased to 0db peak
    (dumb normalization), at the cost of audio quality.

    Args:
        filepath (str): ffmpeg-compatible audio file path.

    Returns:
        float: Maximum volume in dB from ffmpeg output.

    Raises:
        FFMpegExecuteError: If ffmpeg command fails to execute.
        ValueError: If max_volume could not be found in ffmpeg output.

    """
    if filepath.endswith(".pcm"):
        # pcm files have no container with metadata, so we need to specify rate, channels, and format
        # Polly PCM output is 16000Hz, 1-channel, 16-bit signed little-endian
        input_stream: ffmpeg.AudioStream = ffmpeg.input(filepath, ar=16000, ac=1, f="s16le")
    else:
        input_stream: ffmpeg.AudioStream = ffmpeg.input(filepath)

    output_stream: ffmpeg.dag.OutputStream = input_stream.volumedetect().output(filename=os.devnull, f="null")
    # for some reason the output is in stderr instead of stdout
    stderr: str = output_stream.run(capture_stderr=True)[1].decode("utf-8")

    max_volume_match: re.Match[str] | None = re.search(r"max_volume:\s*(-?\d+(\.\d+)?) dB", stderr)
    if not max_volume_match:
        msg = "Could not find max_volume in ffmpeg output."
        raise ValueError(msg)

    max_volume: float = float(max_volume_match.group(1))
    return max_volume


def trim_silence(
    input_stream: ffmpeg.AudioStream,
    min_start_duration: float = 0,
    silence_threshold: str = "-30.0dB",
) -> ffmpeg.AudioStream:
    """Trim silence from the beginning and end of an AudioStream using ffmpeg silenceremove filter.

    https://ffmpeg.org/ffmpeg-filters.html#silenceremove

    Args:
        input_stream (ffmpeg.AudioStream): The input AudioStream to trim.
        min_start_duration (float): "The amount of time that non-silence must be detected before it stops trimming audio."
         Non-zero values treat short noises X seconds long as silence. Default is 0.
        silence_threshold (str): Silence threshold in amplitude, append 'dB' for dB. Default is -30.0dB.

    Returns:
        ffmpeg.AudioStream: The AudioStream with silence trimming filter added.


    """
    # ffmpeg -i input.mp3 -af "
    # silenceremove=start_periods=1:start_duration=x:start_threshold=-30dB:detection=peak,
    # aformat=dblp,  # noqa: ERA001
    # areverse,
    # silenceremove=start_periods=1:start_duration=x:start_threshold=-30dB:detection=peak,
    # aformat=dblp,  # noqa: ERA001
    # areverse" output.mp3

    input_stream = input_stream.silenceremove(
        start_periods=1, start_duration=min_start_duration, start_threshold=silence_threshold, detection="peak"
    )
    input_stream = input_stream.aformat(sample_fmts="dblp")
    input_stream = input_stream.areverse()
    input_stream = input_stream.silenceremove(
        start_periods=1, start_duration=min_start_duration, start_threshold=silence_threshold, detection="peak"
    )
    input_stream = input_stream.aformat(sample_fmts="dblp")
    input_stream = input_stream.areverse()

    return input_stream  # noqa: RET504
