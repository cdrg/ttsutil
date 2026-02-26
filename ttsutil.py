"""Utility functions for processing TTS audio."""

import os
import re
import tempfile
from pathlib import Path

import ffmpeg

poe1_filtersounds_files = {
    "1": "AlertSound1.mp3",
    "2": "AlertSound2.mp3",
    "3": "AlertSound3.mp3",
    "4": "AlertSound4.mp3",
    "5": "AlertSound5.mp3",
    "6": "AlertSound6.mp3",  # Divine tink
    "7": "AlertSound7.mp3",
    "8": "AlertSound8.mp3",
    "9": "AlertSound9.mp3",
    "10": "AlertSound10.mp3",
    "11": "AlertSound11.mp3",
    "12": "AlertSound12.mp3",
    "13": "AlertSound13.mp3",
    "14": "AlertSound14.mp3",
    "15": "AlertSound15.mp3",
    "16": "AlertSound16.mp3",
    "ShAlchemy": "AlertSoundShAlchemy.mp3",
    "ShBlessed": "AlertSoundShBlessed.mp3",
    "ShChaos": "AlertSoundShChaos.mp3",
    "ShDivine": "AlertSoundShDivine.mp3",
    "ShExalted": "AlertSoundShExalted.mp3",
    "ShFusing": "AlertSoundShFusing.mp3",
    "ShGeneral": "AlertSoundShGeneral.mp3",
    "ShMirror": "AlertSoundShMirror.mp3",
    "ShRegal": "AlertSoundShRegal.mp3",
    "ShVaal": "AlertSoundShVaal.mp3",
}

poe1_filtersounds_dir = Path(__file__).parent / "filtersounds"


def get_max_volume(input_file: Path) -> float:
    """Get the maximum volume of an audio file using ffmpeg volumedetect.

    Inverse of return value used with ffmpeg .volume(volume="x.xdB") filter to increase file to 0db max peak.

    When used with "volume" guarantees that the entire file loudness is increased to 0db peak
    (dumb normalization), at the cost of audio quality.

    Args:
        input_file (Path): ffmpeg-compatible audio file path.

    Returns:
        float: Maximum volume in dB from ffmpeg output.

    Raises:
        FFMpegExecuteError: If ffmpeg command fails to execute.
        ValueError: If max_volume could not be found in ffmpeg output.

    """
    if input_file.name.endswith(".pcm"):
        # pcm files have no container with metadata, so we need to specify rate, channels, and format
        # Polly PCM output is 16000Hz, 1-channel, 16-bit signed little-endian
        input_stream: ffmpeg.AudioStream = ffmpeg.input(input_file, ar=16000, ac=1, f="s16le")
    else:
        input_stream: ffmpeg.AudioStream = ffmpeg.input(input_file)

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


def mixin_filtersound(input_file: Path, filtersound_id: str) -> tempfile._TemporaryFileWrapper | None:
    """Mix a specified filtersound from file with an specified AudioStream using ffmpeg amix filter.

    You must acquire the filtersound files yourself. They are available in the game files.
    Expected valid files are listed in poe1_filtersounds_files[].
    Files are expected to be in poe1_filtersounds_dir relative to this file.

    Args:
        input_file (Path): The input file to mix.
        filtersound_id (str): The ID of the filtersound to mix.

    Returns:
        tempfile._TemporaryFileWrapper | None: The mixed temporary file, or None if an error occurred.

    """
    if input_file.name.endswith(".pcm"):
        # pcm files have no container with metadata, so we need to specify rate, channels, and format
        # Polly PCM output is 16000Hz, 1-channel, 16-bit signed little-endian
        input_stream: ffmpeg.AudioStream = ffmpeg.input(input_file, ar=16000, ac=1, f="s16le")
    else:
        input_stream: ffmpeg.AudioStream = ffmpeg.input(input_file)

    filtersound_path: Path = poe1_filtersounds_dir / poe1_filtersounds_files[filtersound_id]
    filtersound_stream: ffmpeg.AudioStream = ffmpeg.input(filtersound_path)

    # Trim silence from input since TTSM results are garbage
    input_stream = trim_silence(input_stream, min_start_duration=0, silence_threshold="-40.0dB")

    mixed_audio: ffmpeg.AudioStream = ffmpeg.filters.amix(
        filtersound_stream, input_stream, inputs=2, duration="longest"
    )

    mixed_file: tempfile._TemporaryFileWrapper = tempfile.NamedTemporaryFile(suffix=(".mp3"), delete=False)  # noqa: SIM115

    output_stream: ffmpeg.dag.OutputStream = mixed_audio.output(
        filename=mixed_file.name, ab="48k", extra_options={"abr": 1}
    )

    try:
        output_stream.run(quiet=True, overwrite_output=True)
    except ffmpeg.FFMpegExecuteError:
        Path(mixed_file.name).unlink(missing_ok=True)  # cleanup temp file if exiting early
        return None

    return mixed_file
