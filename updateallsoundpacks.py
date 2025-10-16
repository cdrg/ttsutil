"""Update all soundpack folders in the specified directory, using the specified TTS template file.

Folder names in the format "<SERVICE>-<Voice>" (e.g. "AWSPolly-Brian") are processed using the 
specified TTS service and voice name/id.

Each soundpack folder must already contain a "sounds" subfolder to be valid.

New soundpacks can be created by creating a new folder with the correct name and a "sounds" subfolder.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import ttsmapi
from boto3 import Session
from botocore.exceptions import ClientError, NoCredentialsError
from mypy_boto3_polly.literals import VoiceIdType  # noqa: TC002

#from mypy_boto3_service_quotas.type_defs import ListServiceQuotasResponseTypeDef  # noqa: TC002
from requests import HTTPError, JSONDecodeError
from ttsmapi.exceptions import TTSMAPIError

import ttsfromtemplate_awspolly
import ttsfromtemplate_ttsmonster

if TYPE_CHECKING:
    from mypy_boto3_polly.client import PollyClient
    #from mypy_boto3_service_quotas.client import ServiceQuotasClient


def update_all_soundpacks(template_file: Path | None = None, 
                             base_dir: Path | None = None) -> int:
    """Update all soundpack folders in the specified directory using the specified TTS template file.

    Args:
        template_file (Path, optional): Path to the template JSON file. 
            Defaults to "template.json".
        base_dir (Path, optional): Directory containing soundpack subdirectories. 
            Defaults to current working directory.

    Returns:
        int: 0 on success, 1 on error

    """
    template_file = template_file or Path("template.json")
    base_dir = base_dir or Path.cwd()

    if not template_file.exists():
        print(f"Error: template file '{template_file}' does not exist.")
        return 1

    if not base_dir.exists() or not base_dir.is_dir():
        print(f"Error: specified directory '{base_dir}' does not exist or is not a directory.")
        return 1

    try:
        with template_file.open(encoding="utf-8") as f:
            template_data = json.load(f)
    except (JSONDecodeError, UnicodeDecodeError) as e:
        print(f"Error reading template: {e}")
        return 1

    # Calculate the total number of characters of 'tts_text' for missing files in all awspolly soundpacks
    awspolly_missing_files: int = 0
    awspolly_missing_chars: int = 0
    awspolly_dirs: list[Path] = [d for d in base_dir.iterdir() if d.is_dir() and d.name.lower().startswith("awspolly-")]
    if awspolly_dirs:
        for awspolly_dir in awspolly_dirs:
            for item in template_data:
                file_fullpath: Path = awspolly_dir / "sounds" / item["path"]

                if not file_fullpath.exists():
                    awspolly_missing_files += 1
                    awspolly_missing_chars += len(item["tts_text"])

        if awspolly_missing_files > 0:
            try: 
                aws_profile: str = os.environ['AWS_PROFILE']
            except KeyError as e:
                print(f"{type(e)}: {e}")
                return 1

            try:
                polly_client: PollyClient = Session(profile_name=aws_profile).client('polly') # type: ignore
            except (ClientError, NoCredentialsError) as e:
                print(f"{type(e)}: {e}")
                return 1

            # TODO: Figure out how to actually get the SynthesizeSpeech quota(s) from AWS Polly
            #service_quotas_client: ServiceQuotasClient = Session(profile_name=aws_profile).client('service-quotas') #type: ignore
            #service_quotas: ListServiceQuotasResponseTypeDef = service_quotas_client.list_service_quotas(ServiceCode='polly')
            #synthesize_speech_quota = next((quota for quota in service_quotas['Quotas'] if quota['QuotaName'] == "SynthesizeSpeech"), None) # type: ignore

            #print(f"AWS Polly account has {synthesize_speech_quota} characters remaining in this billing period.")

    # Calculate the total number of characters of 'tts_text' for missing files in all TTSM soundpacks
    ttsm_missing_files: int = 0
    ttsm_missing_chars: int = 0
    ttsm_dirs: list[Path] = [d for d in base_dir.iterdir() if d.is_dir() and d.name.lower().startswith("ttsm-")]
    if ttsm_dirs:
        for ttsm_dir in ttsm_dirs:
            for item in template_data:
                file_fullpath: Path = ttsm_dir / "sounds" / item["path"]

                if not file_fullpath.exists():
                    ttsm_missing_files += 1
                    ttsm_missing_chars += len(item["tts_text"])

        if ttsm_missing_files > 0:
            try: 
                ttsm_apikey: str = os.environ['TTSMONSTER_API_KEY']
            except KeyError as e:
                print(f"{type(e)}: {e}")
                return 1

            try:
                ttsmapi_client: ttsmapi.Client = ttsmapi.Client(ttsm_apikey)
            except (TTSMAPIError, HTTPError) as e:
                print(f"{type(e)}: {e}")
                return 1

            ttsm_remaining_chars: int = ttsmapi_client.user_info['character_allowance'] - ttsmapi_client.user_info['character_usage']
            print(f"TTSM account has {ttsm_remaining_chars} characters remaining in this billing period.")

    print(f"AWSPolly soundpacks are missing {awspolly_missing_files} files with a total of "
          f"{awspolly_missing_chars} TTS characters.")
    print(f"TTSM soundpacks are missing {ttsm_missing_files} files with a total of "
          f"{ttsm_missing_chars} TTS characters.")

    if ttsm_missing_files > 0 or awspolly_missing_files > 0:
        print("You are responsible for any overage fees. Proceed? (y/n): ", end="")
        choice: str = input().lower()
        if choice != 'y':
            print("'N' selected, exiting.")
            return 0
    else:
        print("No missing TTS files detected in any soundpack dir, exiting.")
        return 0

    print(f"Using template file '{template_file}' to update soundpack folders in '{base_dir}'.")

    if awspolly_missing_files > 0:
        for soundpack_dir in awspolly_dirs:
            voice: str = soundpack_dir.name.split('-', 1)[1]
            sounds_dir: Path = soundpack_dir / 'sounds'
            if not sounds_dir.exists() or not sounds_dir.is_dir():
                print(f"Info: No 'sounds' subfolder in '{soundpack_dir.name}', skipping.")
                continue
            print(f"Creating TTS files in soundpack folder '{soundpack_dir.name}'...")
            retcode: int = ttsfromtemplate_awspolly.ttsfromtemplate_awspolly( 
                polly_client=polly_client, # type: ignore
                voiceid=cast("VoiceIdType", voice),
                template_file=template_file,
                output_dir=soundpack_dir
            )
            if retcode != 0:
                print(f"Error: processing soundpack '{soundpack_dir.name}'.")

    if ttsm_missing_files > 0:
        for soundpack_dir in ttsm_dirs:
            voice: str = soundpack_dir.name.split('-', 1)[1]
            sounds_dir: Path = soundpack_dir / 'sounds'
            if not sounds_dir.exists() or not sounds_dir.is_dir():
                print(f"Info: No 'sounds' subfolder in '{soundpack_dir.name}', skipping.")
                continue
            print(f"Creating TTS files in soundpack folder '{soundpack_dir.name}'...")
            retcode: int = ttsfromtemplate_ttsmonster.ttsfromtemplate_ttsmonster( 
                ttsmapi_client=ttsmapi_client, # type: ignore
                voice=voice,
                template_file=template_file,
                output_dir=soundpack_dir
            )
            if retcode != 0:
                print(f"Error: processing soundpack '{soundpack_dir.name}'.")

    return 0

def main() -> int:
    """Command-line entry point for update_all_soundpacks.

    Returns:
        int: 0 on success, 1 on error

    """
    parser = argparse.ArgumentParser(description="Update all soundpack folders in the specified directory.")
    parser.add_argument("-f", "--file",
                        help="the name of the input json template file",
                        default="template.json")
    parser.add_argument("-d", "--directory",
                        help="the directory containing soundpack subdirectories to update",
                        default=str(Path.cwd()))
    args: argparse.Namespace = parser.parse_args()

    return update_all_soundpacks(Path(args.file), Path(args.directory))

if __name__ == "__main__":
    sys.exit(main())
