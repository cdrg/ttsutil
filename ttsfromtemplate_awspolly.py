"""Update a soundpack voice set of TTS mp3 files from a template.json file using AWS Polly.

For safety, a "sounds" subdirectory must already exist in specified output directory.

AWS boto3 package and configured AWS credentials required.
Currently authorizing with `aws sso login --profile AWS_PROFILE`, but other methods may work.
You must set AWS_PROFILE environment variable to the AWS profile name you want to use.

AWS free tier includes a generous amount of free Polly usage for the first year,
and is inexpensive thereafter.

https://docs.aws.amazon.com/polly/latest/dg/API_Voice.html

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
from contextlib import closing
from json import JSONDecodeError
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

import ffmpeg
from boto3 import Session
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from ffmpeg import FFMpegExecuteError
from mypy_boto3_polly.client import PollyClient
from mypy_boto3_polly.literals import (
    EngineType,
    LanguageCodeType,
    OutputFormatType,
    VoiceIdType,
)

import ttsutil

if TYPE_CHECKING:
    from mypy_boto3_polly.type_defs import SynthesizeSpeechOutputTypeDef


logger = logging.getLogger(__name__)


def ttsfromtemplate_awspolly(
    polly_client: PollyClient,
    voiceid: VoiceIdType,
    template_file: Path | None = None,
    output_dir: Path | None = None,
    languagecode: LanguageCodeType | None = None,
    engine: EngineType = "standard",
    outputformat: OutputFormatType = "mp3",
) -> int:
    """Create a soundpack set of TTS mp3 files from a template json file, using AWS Polly.

    Args:
        polly_client (PollyClient): Boto3 Polly client object
        voiceid (VoiceIdType): AWS Polly voice ID to use, e.g. "Brian"
        template_file (Path, optional): the name of the input json template file. Defaults to "template.json".
        output_dir (Path, optional): the output directory containing a 'sounds' subdirectory to
            create the directory structure and tts files in. Defaults to current working directory.
        languagecode (LanguageCodeType | None, optional): AWS Polly language code to use. Defaults to None.
        engine (EngineType, optional): AWS Polly engine to use. Defaults to "standard".
        outputformat (OutputFormatType, optional): AWS Polly output format to use. Defaults to "mp3".

    Returns:
        (int): 0 on success, 1 on error

    """
    template_file = template_file or Path("template.json")
    output_dir = output_dir or Path.cwd()

    if outputformat == "mp3":
        ffmpeg_input_ext: str = ".mp3"
    elif outputformat == "ogg_vorbis":
        ffmpeg_input_ext: str = ".ogg"
    elif outputformat == "pcm":
        ffmpeg_input_ext: str = ".pcm"
    else:
        logger.error("Error: Invalid Polly output format '%s'", outputformat)
        return 1

    sounds_dir: Path = output_dir / "sounds"

    if not template_file.exists():
        logger.error("Error: template file '%s' does not exist.", template_file)
        return 1
    if not output_dir.exists():
        logger.error("Error: output directory '%s' does not exist.", output_dir)
        return 1
    if not sounds_dir.exists():
        logger.error("Error: required output subdirectory '%s' does not exist.", sounds_dir)
        return 1

    with Path.open(template_file, encoding="utf-8") as f:
        try:
            template_data: list[dict[str, str]] = json.load(f)
        except (JSONDecodeError, UnicodeDecodeError):
            logger.exception("Error reading template file %s", template_file)
            return 1

    created_count: int = 0
    skipped_count: int = 0

    # for each template entry that doesn't already exist as a file, call AWS Polly to create
    # the TTS audio object, then output the returned object to the specified file
    for item in template_data:
        file_partialpath: Path = Path(item["path"])
        tts_text: str = item["tts_text"]
        ssml_text: str = item["ssml_text"]

        file_fullpath: Path = sounds_dir / file_partialpath
        # skip if file already exists
        if file_fullpath.exists():
            skipped_count += 1
            continue

        # need to create output directory (and intermediates) if they don't exist
        if not file_fullpath.parent.exists():
            file_fullpath.parent.mkdir(mode=0o755, parents=True, exist_ok=True)

        # if SSML text exists, set texttype to ssml and use ssml text instead of plain tts text
        if ssml_text:
            texttype: str = "ssml"
            tts_text: str = ssml_text
        else:
            texttype: str = "text"

        # build a kwargs dict for the synthesize_speech call, since languagecode is optional
        synth_speech_kwargs: dict[str, str | VoiceIdType | LanguageCodeType | EngineType | OutputFormatType] = {
            "Text": tts_text,
            "VoiceId": voiceid,
            "TextType": texttype,
            "Engine": engine,
            "OutputFormat": outputformat,
        }
        # if languagecode is omitted, AWS Polly will use the default for the voice
        if languagecode:
            synth_speech_kwargs["LanguageCode"] = languagecode

        try:
            response: SynthesizeSpeechOutputTypeDef = polly_client.synthesize_speech(**synth_speech_kwargs)  # type: ignore
        # if SSML is invalid, log and continue with next item
        except polly_client.exceptions.InvalidSsmlException:
            logger.exception("Invalid SSML for template item: %s", item)
            continue
        except (BotoCoreError, ClientError):
            logger.exception(
                "AWS Polly synthesize_speech failed for template item %s; call: synthesize_speech(text, %s, %s, %s, %s)",
                item,
                voiceid,
                texttype,
                engine,
                outputformat,
            )
            return 1

        if "AudioStream" not in response:
            logger.error("Error: No AudioStream in AWS Polly response")
            return 1

        # use closing to ensure that the close method of the stream is called after the with finishes
        with closing(response["AudioStream"]) as stream:
            try:
                # Write the stream to a temporary file so we can postprocess it with ffmpeg.
                # delete=False so we don't have to worry about staying in the with context
                with tempfile.NamedTemporaryFile(suffix=(ffmpeg_input_ext), delete=False) as f:
                    f.write(stream.read())
            except OSError:
                logger.exception("Error writing temporary audio file")
                return 1

        # suppress ffmpeg INFO log messages
        logging.getLogger("ffmpeg").setLevel(logging.WARNING)

        # Get the max db of the audio file with ffmpeg volumedetect so we can increase file volume.
        try:
            input_max_volume: float = ttsutil.get_max_volume(f.name)
        except (FFMpegExecuteError, ValueError):
            logger.exception("Error processing audio with ffmpeg")
            Path(f.name).unlink(missing_ok=True)  # cleanup temp file if exiting early
            return 1

        # Set ffmpeg input to the the temp file.
        # ffmpeg will intelligently handle format conversion based on the extension of the output file.
        # If input is pcm format, we need to specify rate, channels, format for ffmpeg
        # AWS Polly PCM output is 16000Hz, 1-channel, 16-bit signed little-endian
        if ffmpeg_input_ext == ".pcm":
            input_stream: ffmpeg.AudioStream = ffmpeg.input(f.name, ar=16000, ac=1, f="s16le")
        else:
            input_stream: ffmpeg.AudioStream = ffmpeg.input(f.name)

        # TODO: set speech speed to hit a specific total audio duration, instead of guessing?
        # TODO: Check if selected AWS Polly voice supports SSML? Currently only using SSML voices.
        # If "prosody rate='fast'" is set in SSML text, simulate that with ffmpeg atempo filter.
        # AWS Polly SSML rate='fast' is ~150% (1.5) per experiments.
        # if "rate='fast'" in ssml_text:
        #    input_stream = input_stream.atempo(tempo=1.3)

        # Files must be as loud as possible to be consistently audible in-game.
        # If previously determined peak db is less than -0.5db, use ffmpeg volume filter
        # to increase the file volume by the same amount, resulting in -0.5db peak.
        # Unfortunately, other ffmpeg filters such as loudnorm or dynaudnorm do not
        # work well for our purposes, so we have to do this two-pass method.
        if input_max_volume < -0.5:
            volume_adjustment: float = -input_max_volume - 0.5  # -0.5dB for clipping safety
            input_stream = input_stream.volume(volume=f"{volume_adjustment}dB")

        # Path of Exile is picky about VBR mp3, and ffmpeg seems to default to VBR output,
        # so force CBR with ab= (b:a). Add abr=1 option for fun (hybrid CBR/VBR).
        output_stream: ffmpeg.dag.OutputStream = input_stream.output(
            filename=file_fullpath, ab="48k", extra_options={"abr": 1}
        )

        # Lastly, run ffmpeg.
        try:
            output_stream.run(quiet=True)
        except FFMpegExecuteError:
            logger.exception("ffmpeg failed when writing output file %s", file_fullpath)
            Path(f.name).unlink(missing_ok=True)  # cleanup temp file if exiting early
            return 1

        # cleanup temporary file
        Path(f.name).unlink(missing_ok=True)

        created_count += 1
        # if created_count > 1:
        #     print("\033[1A", end="\x1b[2K")
        logger.info(
            "Created file %d/~%d: %s",
            created_count,
            (len(template_data) - created_count - skipped_count + 1),
            file_fullpath,
        )

    logger.info(
        "Successfully finished. %d file(s) created and %d existing file(s) skipped. %d total.",
        created_count,
        skipped_count,
        created_count + skipped_count,
    )

    return 0


def main() -> int:
    """Command-line entry point for ttsfromtemplate_awspolly.

    Returns:
        int: 0 on success, 1 on error

    """
    parser = argparse.ArgumentParser()
    parser.add_argument("voice", help="AWS Polly voice ID to use", choices=get_args(VoiceIdType))
    parser.add_argument("-f", "--file", help="the name of the input json template file", default="template.json")
    parser.add_argument(
        "-d",
        "--directory",
        help="the output directory containing a 'sounds' subdirectory "
        "to create the directory structure and tts files in",
        default=Path.cwd(),
    )
    parser.add_argument(
        "-l", "--languagecode", help="AWS Polly language code to use", choices=get_args(LanguageCodeType)
    )
    parser.add_argument(
        "-e", "--engine", help="AWS Polly engine to use", choices=get_args(EngineType), default="standard"
    )
    parser.add_argument(
        "-of",
        "--outputformat",
        help="AWS Polly output format to use",
        choices=get_args(OutputFormatType),
        default="mp3",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        help="set exact logging level",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        default=None,
    )
    args: argparse.Namespace = parser.parse_args()

    level: Literal[20] = getattr(logging, args.log_level) if args.log_level else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    try:
        aws_profile: str = os.environ["AWS_PROFILE"]
    except KeyError:
        logger.exception("AWS_PROFILE not set in environment")
        return 1

    try:
        polly_client: PollyClient = Session(profile_name=aws_profile).client("polly")  # type: ignore
    except (ClientError, NoCredentialsError):
        logger.exception("Failed to initialize AWS Polly client")
        return 1

    return ttsfromtemplate_awspolly(
        polly_client=polly_client,
        voiceid=args.voice,
        template_file=Path(args.file),
        output_dir=Path(args.directory),
        languagecode=args.languagecode,
        engine=args.engine,
        outputformat=args.outputformat,
    )


if __name__ == "__main__":
    sys.exit(main())
