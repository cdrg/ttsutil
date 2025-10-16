## ttsutil
Utilities for creating, updating, and managing sets of Text-To-Speech files.

Currently used for creating Path of Exile 1 soundpacks for [poe1filtertts](https://github.com/cdrg/poe1filtertts) and Path of Exile 2 soundpacks for [poe2filtertts](https://github.com/cdrg/poe2filtertts). 

## `template.json` file information
A template.json file is required for generating voice soundpacks. It defines the location, file name, and contents of each TTS file.

It can be created and maintained by hand. It can be created from existing TTS files to start with `createttstemplate.py`.

Example file: [template.json](https://github.com/cdrg/poe2filtertts/blob/main/template.json)

Example entry:
```
[
  {
    "path": "basetypes/amulets/crimson amulet.mp3",
    "tts_text": "crimson amulet",
    "ssml_text": "<prosody rate='fast'>crimson amulet</prosody>"
  }
]
```

## `updateallsoundpacks.py`
Update all voice soundpack folders in the specified directory, using the specified TTS template file.

A batch-updater for updating multiple versions of the same soundpack that use different voices.

Can be run from the command line or called as a function:  
terminal: `poetry run python updateallsoundpacks.py [-h] [-f FILE] [-d DIRECTORY]`  
function: `update_all_soundpacks(template_file: Path, base_dir: Path)`

The first argument is the the template.json file, defaulting to cwd. The second argument is the directory containing soundpack subfolders to update, defaulting to cwd.

## `ttsfromtemplate_awspolly.py`
Update a single voice soundpack set of TTS mp3 files from a template.json file using the AWS Polly service.

Can be run from the command line or called as a function:  
terminal: `poetry run python ttsfromtemplate_awspolly.py [-f FILE] [-d DIRECTORY] [-l LOCALE] [-e ENGINE] [-of OUTPUTFORMAT] voice`  
function: `ttsfromtemplate_awspolly(polly_client: PollyClient, voiceid: VoiceIdType, template_file: Path, output_dir: Path, languagecode: LanguageCodeType | None = None, engine: EngineType = "standard",outputformat: OutputFormatType = "mp3")`

**voice** is a valid AWS Polly voiceID, eg `Brian`. The default locale for that voice will be used, or locale can be optionally specified.

You must have an AWS account and be authorized using `aws sso login --profile` or similar.

Using AWS Polly may incur charges, depending on your AWS account. "AWS free tier" includes a generous amount of free Polly usage for the first year and is inexpensive thereafter.

## `ttsfromtemplate_ttsmonster.py`
Update a single voice soundpack set of TTS mp3 files from a template.json file using the TTS.Monster API.

Can be run from the command line or called as a function:  
terminal: `poetry run python ttsfromtemplate_ttsmonster.py [-f FILE] [-d DIRECTORY] voice`  
function: `ttsfromtemplate_awspolly(ttsmapi_client: ttsmapi.Client, voice: VoiceIdEnum | str, template_file: Path, output_dir: Path)`

**voice** is a valid TTSM voice name or voiceID, eg `Axel` or `24e1a8ff-e5c7-464f-a708-c4fe92c59b28`. Public voices can use voice name, custom voices can currently only use voiceID.

You must have a TTS.Monster account and set your TTSM API key in the TTSMONSTER_API_KEY env variable.

Using TTS.Monster may incur charges, depending on your TTS.Monster account. TTS.Monster free tier includes 10000 characters per month.

## `createttstemplate.py`:
If a `template.json` file for your soundpack does not already exist, this script can create a template.json file from an existing set of soundpack folders and files with:

terminal: `poetry run python createttstemplate.py [-d INPUT_DIRECTORY] [-f OUTPUT_FILE]`

## `ttsutil.py`:
Utility functions used by other scripts, such as increasing loudness to maximum without clipping, or trimming silence.

## `prep_release.py`
Create release assets such as .zip files of TTS voice soundpack directories + audio preview files.

terminal: `prep_release.py [-h] [-i INPUTDIR] [-o OUTPUTDIR] [-f TEMPLATE_FILE]`

## Links:

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/I2I7ROZFD)

[![patreon](https://github.com/user-attachments/assets/b7841f4d-5bcc-4642-a04c-2f22e5c48a24)](https://patreon.com/cdrpt)

[![discord](https://cdn.prod.website-files.com/6257adef93867e50d84d30e2/66e3d74e9607e61eeec9c91b_Logo.svg)](https://discord.gg/gRMjT5gVms)