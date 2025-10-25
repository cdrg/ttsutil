"""Update a soundpack voice set of TTS mp3 files from a template.json file using TTS.Monster.

For safety, a "sounds" subdirectory must already exist in specified output directory.

TTS.Monster API key must be set in environment variable TTSMONSTER_API_KEY

TTS.Monster free tier includes 10000 characters per month.

https://docs.tts.monster/introduction

---

template item json format: {path, tts_text, ssml_text}

path: the relative file path to the sounds dir including all subfolders, filename, extension (.mp3)
    file name should start with the full canonical name of the item, class, etc, then any modifiers
tts_text: the actual text to be TTS read, in plain text;
    for in-game clarity, can be completely different than the filename
ssml_text: (optional) SSML marked up text, empty string if none
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from json import JSONDecodeError
from pathlib import Path
from typing import Literal

import ffmpeg
import requests
import ttsmapi
from requests.exceptions import HTTPError, ReadTimeout
from ttsmapi.enums import VoiceIdEnum
from ttsmapi.exceptions import TTSMAPIError

import ttsutil

logger = logging.getLogger(__name__)


MAX_VOL_DB_OFFSET = -0.5  # max volume normalization offset for safety
SHORT_TEXT_LENGTH = 9  # TTS text length of this or less gets extra TTS file size allowance
BIT_ALLOWANCE_PER_CHAR = 2048  # 2KB per character allowed in output TTS file size
EXTRA_BITS_FOR_SHORT = 1024  # +1KB per character allowance for very short text


def ttsfromtemplate_ttsmonster(
    ttsmapi_client: ttsmapi.Client,
    voice: VoiceIdEnum | str,
    template_file: Path | None = None,
    output_dir: Path | None = None,
    *,
    quality_checks: bool = True,
) -> int:
    """Create a soundpack set of TTS mp3 files from a template JSON file, using TTS.Monster.

    Args:
        ttsmapi_client (ttsmapi.Client): An existing initialized TTS.Monster API client.
        voice (VoiceIdEnum | str): TTS.Monster voice name or id to use. Names are only looked up for
            public voices. Private voices must be specified by UUID.
        template_file (Path, optional): Path to the input JSON template file. Defaults to "template.json".
        output_dir (Path, optional): the output directory containing a 'sounds' subdirectory to
            create the directory structure and tts files in. Defaults to current working directory.
        quality_checks (bool): Whether to perform quality checks on generated TTS files.

    Returns:
        int: 0 on success, 1 on error.

    """
    template_file = template_file or Path("template.json")
    output_dir = output_dir or Path.cwd()

    sounds_dir: Path = output_dir / "sounds"

    if not template_file.exists() or not template_file.is_file():
        logger.error("Template file '%s' does not exist", template_file)
        return 1
    if not output_dir.exists() or not output_dir.is_dir():
        logger.error("Output directory '%s' does not exist", output_dir)
        return 1
    if not sounds_dir.exists() or not sounds_dir.is_dir():
        logger.error("Required subdirectory '%s' does not exist", sounds_dir)
        return 1

    if isinstance(voice, str) and voice.upper() in VoiceIdEnum._member_names_:
        voiceid = str(VoiceIdEnum[voice.upper()].value)
    elif voice in VoiceIdEnum:
        voiceid = str(voice)
    else:
        logger.warning("Specified voice name or ID '%s' does not match any public voice", voice)
        logger.warning("Proceeding with the assumption that it's a private voice ID")
        voiceid = str(voice)

    with Path.open(template_file, encoding="utf-8") as f:
        try:
            template_data: list[dict[str, str]] = json.load(f)
        except (JSONDecodeError, UnicodeDecodeError) as e:
            logger.exception("Failed to load template file: %s", type(e).__name__)
            return 1

    logger.info(
        "TTS.Monster API client ready: Current plan: '%s', Characters used: %d / %d",
        ttsmapi_client.user_info["current_plan"],
        ttsmapi_client.user_info["character_usage"],
        ttsmapi_client.user_info["character_allowance"],
    )

    created_count: int = 0
    skipped_count: int = 0
    total_elapsed: float = 0.0

    # For each template entry that doesn't already exist as a file, call TTS.Monster API to create a TTS file,
    # then retrieve the TTS file from the URL returned by the API.
    for item in template_data:
        file_partialpath: Path = Path(item["path"])
        tts_text: str = item["tts_text"]
        ssml_text: str = item["ssml_text"]  # TTS.Monster does not support SSML, but we may simulate some features

        file_fullpath: Path = sounds_dir / file_partialpath
        # skip if file already exists
        if file_fullpath.exists():
            skipped_count += 1
            continue

        # need to create output directory (and intermediates) if they don't exist
        if not file_fullpath.parent.exists():
            file_fullpath.parent.mkdir(mode=0o755, parents=True, exist_ok=True)

        # print(f"Sent Generate(voice_id={args.voiceid}, message={tts_text})...")
        start_time: float = time.perf_counter()

        try:
            response: dict = ttsmapi_client.generate(voice_id=voiceid, message=tts_text)
        except (ConnectionError, ReadTimeout):
            # On ConnectionError or ReadTimeout, assume TTS.Monster is flaky, just continue to next item
            logger.exception("TTS.M API connection error, skipping item")
            continue
        except (TTSMAPIError, HTTPError):
            logger.exception("TTS.M API error during TTS generation")
            logger.exception("Template item: %s", item)
            logger.exception("Call: generate('%s', '%s')", voice, tts_text)
            return 1

        # print(f"Generate() completed in {time.perf_counter() - start_time:.2f}s")
        total_elapsed += time.perf_counter() - start_time

        # suppress ffmpeg INFO log messages
        logging.getLogger("ffmpeg").setLevel(logging.WARNING)

        if "url" in response:
            # Retrieve the audio file with Requests to a tempfile. Unfortunately necessary because of
            # the need to do two-pass ffmpeg processing. Otherwise ffmpeg could get the file itself.
            try:
                url_response: requests.Response = requests.get(response["url"], timeout=30)
                with tempfile.NamedTemporaryFile(suffix=Path(response["url"]).suffix) as f:
                    f.write(url_response.content)
                    try:
                        max_volume: float = ttsutil.get_max_volume(f.name)
                    except ValueError:
                        logger.exception("Could not determine max volume of audio file")
                        return 1

                    # Set ffmpeg input to the tempfile.
                    # ffmpeg will intelligently handle format conversion based on the extension of the output file.
                    input_stream: ffmpeg.AudioStream = ffmpeg.input(f.name)

                    # If "prosody rate='fast'" is set in SSML text, simulate that with ffmpeg atempo filter.
                    # AWS Polly SSML rate='fast' is ~150% (1.5) per experiments.
                    if "rate='fast'" in ssml_text:
                        input_stream = input_stream.atempo(tempo=1.3)

                    # TODO: trim silence, since TTS.Monster models are unstable and sometimes emit lengthy silence,
                    # among other issues.
                    # should probably first try passing an AudioStream to ttsutil.trim_silence(),
                    # returning the modified AudioStream, and if that doesn't work, write to a file and pass that back.
                    # ttsutil.trim_silence(input_stream, silence_threshold=-30.0, min_silence_duration=0.2)

                    # Files must be as loud as possible to be consistently audible in-game.
                    # If previously determined peak db is less than -0.5db, use ffmpeg volume filter
                    # to increase the file volume by the same amount, resulting in -0.5db peak.
                    # Unfortunately, other ffmpeg filters such as loudnorm or dynaudnorm do not
                    # work well for our purposes.
                    if max_volume < MAX_VOL_DB_OFFSET:
                        volume_adjustment: float = -max_volume - MAX_VOL_DB_OFFSET
                        input_stream = input_stream.volume(volume=f"{volume_adjustment}dB")

                    # Lastly, set the output file and run ffmpeg.
                    input_stream.output(filename=file_fullpath).run(quiet=True)

                    # Reject the file if it's too long/too large since that indicates the
                    # TTS.Monster model failed to produce reasonable audio output.
                    # Just delete the file, exiting loop with error not desirable.
                    if quality_checks:
                        max_size: int = BIT_ALLOWANCE_PER_CHAR * len(tts_text)
                        if len(tts_text) <= SHORT_TEXT_LENGTH:
                            max_size += EXTRA_BITS_FOR_SHORT * len(tts_text)

                        if file_fullpath.stat().st_size > max_size:
                            logger.warning(
                                "Output %s is too large (%dKB), removing it",
                                file_fullpath,
                                file_fullpath.stat().st_size // 1024,
                            )
                            file_fullpath.unlink()
                            # implement a retry mechanism?
                            continue

            except (OSError, HTTPError, ReadTimeout):
                logger.exception("Failed to get audio file from TTS.Monster returned URL")
                return 1
        else:
            logger.error("No audio URL in TTS.Monster response")
            return 1

        created_count += 1
        # if created_count > 1:
        #    print("\033[1A", end="\x1b[2K")
        logger.info(
            "Created file %d/~%d: %s. Used: %d/%d chars",
            created_count,
            len(template_data) - created_count - skipped_count + 1,
            file_fullpath,
            response["characterUsage"],
            ttsmapi_client.user_info["character_allowance"],
        )

    logger.info(
        "Successfully finished. %d file(s) created and %d existing file(s) skipped in %s. %d total.",
        created_count,
        skipped_count,
        time.strftime("%Mm:%Ss", time.gmtime(total_elapsed)),
        created_count + skipped_count,
    )
    return 0


def main() -> int:
    """Command-line entry point for ttsfromtemplate_ttsmonster.

    Returns:
        int: 0 on success, 1 on error

    """
    parser = argparse.ArgumentParser()
    parser.add_argument("voiceid", help="TTS.Monster voice name or voice ID to use")
    parser.add_argument("-f", "--file", help="the name of the input json template file", default="template.json")
    parser.add_argument(
        "-d",
        "--directory",
        help="the output directory containing a 'sounds' subdirectory "
        "to create the directory structure and tts files in",
        default=Path.cwd(),
    )
    parser.add_argument(
        "--skipqa",
        help="disable quality checks for generated TTS files",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--ignorequota",
        help="ignore character quota limits in TTS.Monster API client, will incur overage charges if exceeded",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "-l",
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="set the logging output level",
    )
    args: argparse.Namespace = parser.parse_args()

    level: Literal[20] = getattr(logging, args.log_level) if args.log_level else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    try:
        ttsm_apikey: str = os.environ["TTSMONSTER_API_KEY"]
    except KeyError:
        logger.exception("TTSMONSTER_API_KEY environment variable is not set")
        return 1

    try:
        ttsmapi_client: ttsmapi.Client = ttsmapi.Client(
            api_key=ttsm_apikey, enforce_char_quota=not bool(args.ignorequota)
        )
    except (TTSMAPIError, HTTPError):
        logger.exception("TTS.M API client initialization failed")
        return 1

    return ttsfromtemplate_ttsmonster(
        ttsmapi_client=ttsmapi_client,
        voice=args.voiceid,
        template_file=Path(args.file),
        output_dir=Path(args.directory),
        quality_checks=not bool(args.skipqa),
    )


if __name__ == "__main__":
    sys.exit(main())
