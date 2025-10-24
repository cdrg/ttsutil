# ruff: noqa: T201
"""Create release assets such as .zip files of TTS voice soundpack directories + audio preview files."""

import argparse
import json
import shutil
import sys
from pathlib import Path

import ffmpeg


def main() -> int:
    """Make .zip files of TTS soundpack directories for uploading with a github release.

    Also create other release assets such as preview audio files (in video containers).

    Warning: Existing files with the same name in the output directory will be overwritten.

    Returns:
        (int): 0 on success, 1 on error

    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-i",
        "--inputdir",
        help="the input directory containing soundpack dirs",
        default=str(Path.cwd()),
    )
    parser.add_argument(
        "-o",
        "--outputdir",
        help="the output directory to write zips to",
        default=str(Path.home() / "ttszips"),
    )
    parser.add_argument("-f", "--file", help="the name of the input json template file", default="template.json")
    args: argparse.Namespace = parser.parse_args()

    inputdir = Path(args.inputdir)
    outputdir = Path(args.outputdir)

    if not inputdir.exists():
        print(f"Error: input directory '{inputdir}' does not exist.")
        return 1

    if not outputdir.exists():
        outputdir.mkdir(mode=0o755, parents=True, exist_ok=True)

    with Path.open(args.file, encoding="utf-8") as f:
        try:
            template: list[dict[str, str]] = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"{type(e)}: {e}")
            return 1

    soundpackdirs: list[str] = [p.name for p in inputdir.iterdir() if p.is_dir() and (p / "sounds").is_dir()]

    # Check if all files in the template exist in each soundpack directory
    missing_file: bool = False
    for soundpackdir in soundpackdirs:
        for item in template:
            file_partialpath: str = item["path"]
            file_fullpath: Path = inputdir / soundpackdir / "sounds" / file_partialpath
            # skip if file already exists
            if not file_fullpath.exists():
                missing_file = True
                print(f"Warning: TTS file '{file_fullpath}' does not exist in soundpack directory '{soundpackdir}'")

    if missing_file:
        print("TTS files specified in template are missing in one or more soundpack directories.")
        if input("Continue anyway? y/n: ").strip().lower() != "y":
            return 1
    else:
        print("Successful check: All TTS files specified in template file exist in all soundpack directories.")

    # Check if any .mp3 files in each soundpack directory do not exist in the template
    extra_file: bool = False
    for soundpackdir in soundpackdirs:
        soundpackdir_path: Path = inputdir / soundpackdir
        sounds_root = soundpackdir_path / "sounds"
        for path in sounds_root.rglob("*.mp3"):
            root = str(path.parent)
            filename = path.name
            # skip non-.mp3 files (rglob already filters)
            # skip files in voicelines dir since they're not TTS files
            # skip quest_item.mp3 since it's not a TTS file
            if "voicelines" in root or filename == "quest item.mp3":
                continue

            # get path relative to sounds dir
            entry_path: str = str(path.relative_to(sounds_root)).lstrip("/")

            # skip if entry path already exists in template list
            if not any(entry["path"] == entry_path for entry in template):
                extra_file = True
                print(f"Warning: File '{path}' does not exist in template file.")

    if extra_file:
        print("One or more .mp3 files in a soundpack directory do not exist in template file.")
        if input("Continue anyway? y/n: ").strip().lower() != "y":
            return 1
    else:
        print("Successful check: All .mp3 files in soundpack directories exist in template file.")

    # Generate release files
    for soundpackdir in soundpackdirs:
        soundpackdir_path: Path = inputdir / soundpackdir

        zip_path: Path = outputdir / soundpackdir

        # Make a zip file of the soundpack directory
        shutil.make_archive(base_name=str(zip_path), format="zip", root_dir=str(soundpackdir_path))

        # Make a preview audio file of the soundpack.
        # Github README.md only supports video embeds, so put the audio in a video container.
        preview_input_path: Path = soundpackdir_path / "sounds" / "currency" / "chaos orb.mp3"
        preview_output_path: Path = outputdir / f"{soundpackdir}.mp4"
        ffmpeg.input(str(preview_input_path)).output(filename=str(preview_output_path)).run(
            quiet=True, overwrite_output=True
        )

        print(
            f"Created zip '{zip_path}'.zip and preview '{preview_output_path}' from soundpack dir '{soundpackdir_path}'"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
