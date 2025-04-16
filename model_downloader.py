import time

import aiohttp
import os
import zipfile
import shutil
import re
import json
from logging import getLogger
from pathlib import Path
import asyncio

log = getLogger(__name__)

url_pattern = re.compile(
    r'^(https?://)?'  
    r'(www\.)?'        
    r'([a-zA-Z0-9\-_]+)' 
    r'(\.[a-zA-Z]{2,})'  
    r'(/[a-zA-Z0-9\-_/]*)?' 
    r'(\?[a-zA-Z0-9\-_=&]*)?'  
    r'(#\w*)?$'
)

MODELS_PATH = "Models/"

with open("MODELS.json", 'r') as f:
    MODELS_LINKS = json.loads(f.read())

with open("MODEL_MAPPER.json", 'r') as f:
    try:
        UNZIP_PATH_NAME = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error loading JSON file: {e}")
        UNZIP_PATH_NAME = {}

async def __download__(url = None, language: str = "") -> Path | str:
    # if not url or not url_pattern.match(url):
    #     return None
    os.makedirs("Models", exist_ok=True)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.ok:
                with open(f"{MODELS_PATH}{language}.zip", "wb") as f:
                    f.write(await response.read())
                    return MODELS_PATH + (language + ".zip")
            else:
                return f"Download error {response.status}"

def safe_remove(path):
    if not os.path.isfile(path):
        return
    for _ in range(3):
        try:
            os.remove(path)
            break
        except PermissionError:
            time.sleep(1)

def unzip(path, extract_to):
    with zipfile.ZipFile(path, "r") as zip_ref:
        zip_ref.extractall(extract_to)
    safe_remove(path)
    extracted_folders = [f for f in os.listdir(extract_to) if f.startswith("vosk-model-") and os.path.isdir(os.path.join(extract_to, f))]
    if extracted_folders:
        return os.path.join(extract_to, extracted_folders[0])

    return "Extraction completed, but no 'vosk-model-' folder found."

def check_path(path) -> bool:
    """Check the Models path for models"""
    if "Models" not in path:
        return False
    return os.path.isdir(path)

def rename(path = None, name=None):
    if not path or not name:
        log.error("Invalid path or name provided")
        return "Invalid path or name provided"

    try:
        os.makedirs(os.path.dirname(name), exist_ok=True)
        shutil.move(path, name)
        log.info(f"Rename file in {path} -> {name} Successfully")
        return "OK"
    except OSError as e:
        log.error(e)
        return f"Error renaming file: {e}"


async def run_task():
    for language, url in MODELS_LINKS.items():
        log.info(f"Starting Download Models {language} - {url}")
        if len(UNZIP_PATH_NAME) == 0:
            log.error("Fail to map language foldername")
            return
        zip_path = await __download__(url, UNZIP_PATH_NAME.get(language))
        if not zip_path:
            log.error(f"Download fail XD - language: {language}, {zip_path}")
            return

        extract_to = f"Models/"
        extract_path = unzip(zip_path, extract_to)
        rename(extract_path, os.path.join(extract_to, UNZIP_PATH_NAME.get(language)))

# DEBUG ...?
if __name__ == '__main__':
    asyncio.run(run_task())


"""
ALL VOICE MODEL CREDIT TO: https://alphacephei.com/vosk/models

FOR MORE VOICE MODEL, GO TO THAT WEBSITE ...????

ありがとう！！！
"""