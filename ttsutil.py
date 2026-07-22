"""Utility functions for processing TTS audio."""

import os
import re
import tempfile
from pathlib import Path

import ffmpeg

poe1_filtersounds_files = {
    "1": "AlertSound_01.wav",  # A tier
    "2": "AlertSound_02.wav",  # B, C, D, E tier
    "3": "AlertSound_03.wav",
    "4": "AlertSound_04.wav",
    "5": "AlertSound_05.wav",
    "6": "AlertSound_06.wav",  # S tier / "Divine tink"
    "7": "AlertSound_07.wav",
    "8": "AlertSound_08.wav",
    "9": "AlertSound_09.wav",
    "10": "AlertSound_10.wav",
    "11": "AlertSound_11.wav",
    "12": "AlertSound_12.wav",
    "13": "AlertSound_13.wav",
    "14": "AlertSound_14.wav",
    "15": "AlertSound_15.wav",
    "16": "AlertSound_16.wav",
    "ShAlchemy": "SH22Alchemy.wav",
    "ShBlessed": "SH22Blessed.wav",
    "ShChaos": "SH22Chaos.wav",
    "ShDivine": "SH22Divine.wav",
    "ShExalted": "SH22Exalted.wav",
    "ShFusing": "SH22Fusing.wav",
    "ShGeneral": "SH22General.wav",
    "ShMirror": "SH22Mirror.wav",
    "ShRegal": "SH22Regal.wav",
    "ShVaal": "SH22Vaal.wav",
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


def mixin_filtersound(tts_input_file: Path, filtersound_id: str) -> tempfile._TemporaryFileWrapper | None:
    """Mix a specified filtersound from file with an specified AudioStream using ffmpeg amix filter.

    You must acquire the filtersound files yourself. They are available in the game files.
    Expected valid files are listed in poe1_filtersounds_files[].
    Files are expected to be in poe1_filtersounds_dir relative to this file.

    Args:
        tts_input_file (Path): The input file to mix.
        filtersound_id (str): The ID of the filtersound to mix.

    Returns:
        tempfile._TemporaryFileWrapper | None: The mixed temporary file, or None if an error occurred.

    """
    if tts_input_file.name.endswith(".pcm"):
        # pcm files have no container with metadata, so we need to specify rate, channels, and format
        # Polly PCM output is 16000Hz, 1-channel, 16-bit signed little-endian
        tts_stream: ffmpeg.AudioStream = ffmpeg.input(tts_input_file, ar=16000, ac=1, f="s16le")
    else:
        tts_stream: ffmpeg.AudioStream = ffmpeg.input(tts_input_file)

    filtersound_path: Path = poe1_filtersounds_dir / poe1_filtersounds_files[filtersound_id]
    filtersound_stream: ffmpeg.AudioStream = ffmpeg.input(filtersound_path)

    # Trim any silence from input since TTSM results are garbage
    tts_stream = trim_silence(tts_stream, min_start_duration=0, silence_threshold="-40.0dB")

    # # It turned out that ducking did not noticeably improve quality
    # tts_sidechain_mix = tts_stream.asplit()  # noqa: ERA001

    # # Duck filtersound using TTS sidechain
    # ducked = filtersound_stream.sidechaincompress(  # noqa: ERA001
    #     tts_sidechain_mix.audio(0),  # noqa: ERA001
    #     threshold=0.05,  # noqa: ERA001
    #     ratio=8,  # noqa: ERA001
    #     attack=5,  # noqa: ERA001
    #     release=200,  # noqa: ERA001
    # )  # noqa: ERA001

    # # Mix ducked filtersound + original TTS
    # mixed_audio = ffmpeg.filters.amix(ducked, tts_sidechain_mix.audio(1), inputs=2, duration="longest")  # noqa: ERA001

    mixed_audio: ffmpeg.AudioStream = ffmpeg.filters.amix(filtersound_stream, tts_stream, inputs=2, duration="longest")

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
