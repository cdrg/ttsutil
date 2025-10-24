"""Update all soundpack folders in the specified directory, using the specified TTS template file.

Folder names in the format "<SERVICE>-<Voice>" (e.g. "AWSPolly-Brian") are processed using the
specified TTS service and voice name/id.

Each soundpack folder must already contain a "sounds" subfolder to be valid.

New soundpacks can be created by creating a new folder with the correct name and a "sounds" subfolder.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import ttsmapi
from boto3 import Session
from botocore.exceptions import ClientError, NoCredentialsError
from mypy_boto3_polly.literals import VoiceIdType  # noqa: TC002

# from mypy_boto3_service_quotas.type_defs import ListServiceQuotasResponseTypeDef  # noqa: TC002
from requests import HTTPError, JSONDecodeError
from ttsmapi.exceptions import TTSMAPIError

import ttsfromtemplate_awspolly
import ttsfromtemplate_ttsmonster

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from mypy_boto3_polly.client import PollyClient
    # from mypy_boto3_service_quotas.client import ServiceQuotasClient


def _count_missing_for_service(
    base_dir: Path, template_data: list[dict], dir_prefix: str, *, log_missing: bool = False
) -> tuple[list[Path], int, int]:
    """Return (dirs, num_missing_files, num_missing_chars) for soundpacks matching prefix.

    Args:
        base_dir (Path): parent directory containing soundpack directories
        template_data (list[dict]): loaded template data
        dir_prefix (str): lower-case prefix to match directory names (e.g. 'awspolly-')
        log_missing (bool): if True, log each missing file path. Default False.

    Returns:
        tuple[list[Path], int, int]: (list of matching dirs, number of missing files, number of missing TTS characters)

    """
    dirs: list[Path] = [d for d in base_dir.iterdir() if d.is_dir() and d.name.lower().startswith(dir_prefix)]
    num_missing_files = 0
    num_missing_chars = 0
    for pack_dir in dirs:
        for item in template_data:
            file_fullpath: Path = pack_dir / "sounds" / item["path"]
            if not file_fullpath.exists():
                num_missing_files += 1
                num_missing_chars += len(item.get("tts_text", ""))
                if log_missing:
                    logger.info("Missing: %s", file_fullpath)
    return dirs, num_missing_files, num_missing_chars


def update_all_soundpacks(
    template_file: Path | None = None, base_dir: Path | None = None, *, log_missing: bool = False
) -> int:
    """Update all soundpack folders in the specified directory using the specified TTS template file.

    Args:
        template_file (Path, optional): Path to the template JSON file.
            Defaults to "template.json".
        base_dir (Path, optional): Directory containing soundpack subdirectories.
            Defaults to current working directory.
        log_missing (bool, optional): If True, log each missing TTS file path. Default False.

    Returns:
        int: 0 on success, 1 on error

    """
    template_file = template_file or Path("template.json")
    base_dir = base_dir or Path.cwd()

    if not template_file.exists():
        logger.error("Error: template file '%s' does not exist.", template_file)
        return 1

    if not base_dir.exists() or not base_dir.is_dir():
        logger.error("Error: specified directory '%s' does not exist or is not a directory.", base_dir)
        return 1

    try:
        with template_file.open(encoding="utf-8") as f:
            template_data = json.load(f)
    except (JSONDecodeError, UnicodeDecodeError):
        logger.exception("Error reading template")
        return 1

    # Calculate the total number of characters of 'tts_text' for missing files in all awspolly soundpacks
    awspolly_dirs, awspolly_num_missing_files, awspolly_num_missing_chars = _count_missing_for_service(
        base_dir=base_dir, template_data=template_data, dir_prefix="awspolly-", log_missing=log_missing
    )
    if awspolly_num_missing_files > 0:
        try:
            aws_profile: str = os.environ["AWS_PROFILE"]
        except KeyError:
            logger.exception("AWS_PROFILE not set in environment")
            return 1

        try:
            polly_client: PollyClient = Session(profile_name=aws_profile).client("polly")  # type: ignore[attr-defined]
        except (ClientError, NoCredentialsError):
            logger.exception("Failed to initialize AWS Polly client")
            return 1

        # TODO(cdr): Figure out how to actually get the SynthesizeSpeech quota(s) from AWS Polly
        # service_quotas_client: ServiceQuotasClient = Session(profile_name=aws_profile).client('service-quotas')  # type: ignore[attr-defined]
        # service_quotas: ListServiceQuotasResponseTypeDef = service_quotas_client.list_service_quotas(ServiceCode='polly')
        # synthesize_speech_quota = next((quota for quota in service_quotas['Quotas'] if quota['QuotaName'] == "SynthesizeSpeech"), None)  # type: ignore

        # logger.info("AWS Polly account has %s characters remaining in this billing period.", synthesize_speech_quota)

    # Calculate the total number of characters of 'tts_text' for missing files in all TTSM soundpacks
    ttsm_dirs, ttsm_num_missing_files, ttsm_num_missing_chars = _count_missing_for_service(
        base_dir=base_dir, template_data=template_data, dir_prefix="ttsm-", log_missing=log_missing
    )
    if ttsm_num_missing_files > 0:
        try:
            ttsm_apikey: str = os.environ["TTSMONSTER_API_KEY"]
        except KeyError:
            logger.exception("TTSMONSTER_API_KEY not set in environment")
            return 1

        try:
            ttsmapi_client: ttsmapi.Client = ttsmapi.Client(ttsm_apikey)
        except (TTSMAPIError, HTTPError):
            logger.exception("Failed to initialize TTS.Monster API client")
            return 1

        ttsm_remaining_chars: int = (
            ttsmapi_client.user_info["character_allowance"] - ttsmapi_client.user_info["character_usage"]
        )
        logger.info("TTSM account has %d characters remaining in this billing period.", ttsm_remaining_chars)

    logger.info(
        "AWSPolly soundpacks are missing %d files with a total of %d TTS characters.",
        awspolly_num_missing_files,
        awspolly_num_missing_chars,
    )
    logger.info(
        "TTSM soundpacks are missing %d files with a total of %d TTS characters.",
        ttsm_num_missing_files,
        ttsm_num_missing_chars,
    )

    if ttsm_num_missing_files > 0 or awspolly_num_missing_files > 0:
        prompt = "You are responsible for any TTS generation fees incurred. Proceed? (y/n): "
        if input(prompt).strip().lower() != "y":
            print("'n' selected, exiting.")  # noqa: T201
            return 0
    else:
        logger.info("No TTS files specified in template are missing in any soundpack dir, exiting.")
        return 0

    logger.info("Using template file '%s' to update soundpack folders in '%s'.", template_file, base_dir)

    if awspolly_num_missing_files > 0:
        for soundpack_dir in awspolly_dirs:
            voice: str = soundpack_dir.name.split("-", 1)[1]
            sounds_dir: Path = soundpack_dir / "sounds"
            if not sounds_dir.exists() or not sounds_dir.is_dir():
                logger.info("No 'sounds' subfolder in '%s', skipping.", soundpack_dir.name)
                continue
            logger.info("Creating TTS files in soundpack folder '%s'...", soundpack_dir.name)
            retcode: int = ttsfromtemplate_awspolly.ttsfromtemplate_awspolly(
                polly_client=polly_client,  # type: ignore
                voiceid=cast("VoiceIdType", voice),
                template_file=template_file,
                output_dir=soundpack_dir,
            )
            if retcode != 0:
                logger.error("Error: processing soundpack '%s'.", soundpack_dir.name)

    if ttsm_num_missing_files > 0:
        for soundpack_dir in ttsm_dirs:
            voice: str = soundpack_dir.name.split("-", 1)[1]
            sounds_dir: Path = soundpack_dir / "sounds"
            if not sounds_dir.exists() or not sounds_dir.is_dir():
                logger.info("No 'sounds' subfolder in '%s', skipping.", soundpack_dir.name)
                continue
            logger.info("Creating TTS files in soundpack folder '%s'...", soundpack_dir.name)
            retcode: int = ttsfromtemplate_ttsmonster.ttsfromtemplate_ttsmonster(
                ttsmapi_client=ttsmapi_client,  # type: ignore
                voice=voice,
                template_file=template_file,
                output_dir=soundpack_dir,
            )
            if retcode != 0:
                logger.error("Error: processing soundpack '%s'.", soundpack_dir.name)

    return 0


def main() -> int:
    """Command-line entry point for update_all_soundpacks.

    Returns:
        int: 0 on success, 1 on error

    """
    parser = argparse.ArgumentParser(description="Update all soundpack folders in the specified directory.")
    parser.add_argument("-f", "--file", help="the name of the input json template file", default="template.json")
    parser.add_argument(
        "-d", "--directory", help="the directory containing soundpack subdirectories to update", default=str(Path.cwd())
    )
    parser.add_argument("-m", "--missing", help="output list of missing TTS files", action="store_true")
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

    return update_all_soundpacks(template_file=Path(args.file), base_dir=Path(args.directory), log_missing=args.missing)


if __name__ == "__main__":
    sys.exit(main())
