import time
import aiohttp
import zipfile
import shutil
import json
import logging
from pathlib import Path
import asyncio
from tqdm.asyncio import tqdm
import core.logger # noqa

log = logging.getLogger(__name__)

MODELS_DIR = Path("Models")
MODELS_DIR.mkdir(exist_ok=True)

try:
    with open("MODELS.json", 'r') as f:
        MODELS_LINKS = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    log.error(f"Error loading MODELS.json: {e}")
    MODELS_LINKS = {}

try:
    with open("MODEL_MAPPER.json", 'r') as f:
        MODEL_TARGET_NAMES = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    log.error(f"Error loading MODEL_MAPPER.json: {e}")
    MODEL_TARGET_NAMES = {}


class ModelDownloadError(Exception):
    """Custom exception for download errors."""
    pass

class ModelExtractionError(Exception):
    """Custom exception for extraction errors."""
    pass

async def download_file(session: aiohttp.ClientSession, url: str, destination: Path) -> None:
    """Downloads a file from a URL to a destination path with a progress bar."""
    log.info(f"Attempting download: {url} -> {destination}")
    progress_bar = None
    try:
        async with session.get(url) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))

            progress_bar = tqdm(
                total=total_size,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
                desc=f"Downloading {destination.name}",
                leave=False
            )

            try:
                with open(destination, "wb") as f:
                    downloaded_size = 0
                    async for chunk in response.content.iter_chunked(8192):
                        if not chunk:
                            break
                        size = f.write(chunk)
                        downloaded_size += size
                        if progress_bar:
                            progress_bar.update(size)

                if total_size != 0 and downloaded_size < total_size:
                    raise ModelDownloadError(f"Download incomplete for {destination.name}: {downloaded_size}/{total_size} bytes.")

            finally:
                if progress_bar:
                    progress_bar.close()

                log.info(f"Successfully downloaded: {destination}")

    except aiohttp.ClientError as e:
        log.error(f"Download failed for {url}: {e}")
        if destination.exists():
            safe_remove(destination)
        raise ModelDownloadError(f"Failed to download {url}: {e}") from e
    except Exception as e:
        log.error(f"An unexpected error occurred during download of {url}: {e}")
        if destination.exists():
            safe_remove(destination)
        if progress_bar:
            progress_bar.close()
        raise ModelDownloadError(f"Unexpected error downloading {url}: {e}") from e


def safe_remove(path: Path):
    """Safely removes a file with retries on PermissionError."""
    if not path.is_file():
        log.debug(f"Attempted to remove non-existent file: {path}")
        return
    for attempt in range(3):
        try:
            path.unlink()
            log.debug(f"Removed file: {path}")
            return
        except PermissionError:
            log.warning(f"PermissionError removing {path}, attempt {attempt + 1}/3. Retrying after 1s...")
            time.sleep(1)
        except Exception as e:
            log.error(f"Error removing file {path}: {e}")
            return
    log.error(f"Failed to remove file {path} after multiple attempts.")


def unzip_and_find_model(zip_path: Path, extract_to: Path) -> Path:
    """Unzips an archive and finds the 'vosk-model-*' directory."""
    log.info(f"Extracting {zip_path} to {extract_to}")
    extracted_folder_name = None
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            vosk_dirs = [Path(f).name for f in zip_ref.namelist() if f.startswith("vosk-model-") and f.endswith('/')]
            if not vosk_dirs:
                all_members = zip_ref.namelist()
                for member in all_members:
                    parts = Path(member).parts
                    if len(parts) > 1 and parts[0].startswith("vosk-model-") and parts[0] not in vosk_dirs:
                        vosk_dirs.append(parts[0])

            if not vosk_dirs:
                raise ModelExtractionError(f"No directory starting with 'vosk-model-' found in {zip_path.name}")
            if len(vosk_dirs) > 1:
                log.warning(f"Multiple potential model folders found in {zip_path.name}: {vosk_dirs}. Using '{vosk_dirs[0]}'.")

            extracted_folder_name = vosk_dirs[0]

            log.info(f"Starting extraction of {zip_path.name}...")
            zip_ref.extractall(extract_to)
            log.info(f"Finished extraction of {zip_path.name}.")


        extracted_path = extract_to / extracted_folder_name
        if not extracted_path.is_dir():
            potential_path = extract_to / zip_path.stem
            if potential_path.is_dir() and any((potential_path / item).exists() for item in ["am", "conf", "graph", "ivector"]):
                log.warning(f"Model content possibly extracted directly into {potential_path}, using it.")
                extracted_path = potential_path
                extracted_folder_name = potential_path.name
            else:
                raise ModelExtractionError(f"Extracted path {extracted_path} is not a directory after unzip.")

        log.info(f"Successfully identified extracted model folder: {extracted_path}")
        return extracted_path
    except (zipfile.BadZipFile, OSError, ModelExtractionError) as e:
        log.error(f"Failed to extract {zip_path.name}: {e}")
        if extracted_folder_name:
            cleanup_path = extract_to / extracted_folder_name
            if cleanup_path.is_dir():
                try:
                    shutil.rmtree(cleanup_path)
                    log.info(f"Cleaned up partially extracted folder: {cleanup_path}")
                except OSError as rm_err:
                    log.error(f"Could not clean up directory {cleanup_path}: {rm_err}")
        raise
    finally:
        safe_remove(zip_path)


def rename_model_dir(source_path: Path, target_name: str, base_dir: Path) -> Path:
    """Renames the extracted model directory."""
    target_path = base_dir / target_name
    log.info(f"Renaming {source_path.name} -> {target_path.name}")
    try:
        target_path.parent.mkdir(exist_ok=True, parents=True)
        if target_path.exists():
            log.warning(f"Target path {target_path} exists unexpectedly before rename. Attempting to remove.")
            try:
                if target_path.is_dir():
                    shutil.rmtree(target_path)
                else:
                    target_path.unlink()
            except OSError as e:
                log.error(f"Could not remove pre-existing target {target_path} before rename: {e}")
                raise

        shutil.move(str(source_path), str(target_path))
        log.info(f"Successfully renamed model to {target_path}")
        return target_path
    except OSError as e:
        log.error(f"Error renaming {source_path} to {target_path}: {e}")
        if source_path.exists() and source_path.is_dir():
            try:
                shutil.rmtree(source_path)
                log.info(f"Cleaned up source directory after failed rename: {source_path}")
            except OSError as rm_err:
                log.error(f"Could not clean up source directory {source_path}: {rm_err}")
        raise


async def process_model(session: aiohttp.ClientSession, language: str, url: str):
    """Handles download, unzip, and rename for a single model, checking existence first."""
    log.info(f"--- Processing model for language: {language} ---")

    target_name = MODEL_TARGET_NAMES.get(language)
    if not target_name:
        log.error(f"No target name found in MODEL_MAPPER.json for language: {language}. Skipping.")
        return

    final_model_path = MODELS_DIR / target_name

    if final_model_path.is_dir():
        is_valid = (final_model_path / "am").is_dir() and \
                   (final_model_path / "conf").is_dir()

        if is_valid:
            log.info(f"Model '{target_name}' already exists and seems valid. Skipping.")
            return
        else:
            log.warning(f"Model directory '{target_name}' exists but looks incomplete or invalid. Removing and re-downloading.")
            try:
                shutil.rmtree(final_model_path)
                log.info(f"Removed existing incomplete directory: {final_model_path}")
            except OSError as e:
                log.error(f"Could not remove existing incomplete model directory {final_model_path}: {e}. Skipping processing for {language}.")
                return

    zip_filename = f"{target_name}.zip"
    zip_path = MODELS_DIR / zip_filename

    try:
        await download_file(session, url, zip_path)
        extracted_path = unzip_and_find_model(zip_path, MODELS_DIR)
        final_path = rename_model_dir(extracted_path, target_name, MODELS_DIR)
        log.info(f"Successfully processed model for {language}. Final path: {final_path}")

    except (ModelDownloadError, ModelExtractionError, OSError, Exception) as e:
        log.error(f"Failed to process model for language '{language}': {e}")

    finally:
        if zip_path.exists() and 'final_path' not in locals():
            log.debug(f"Cleaning up zip file {zip_path} after error during processing.")
            safe_remove(zip_path)


async def download_all_models():
    """Downloads and processes all models concurrently."""
    if not MODELS_LINKS or not MODEL_TARGET_NAMES:
        log.error("MODEL_LINKS or MODEL_TARGET_NAMES is empty. Check JSON files. Aborting.")
        return

    num_models = len(MODELS_LINKS)
    log.info(f"Found {num_models} models to potentially process.")

    async with aiohttp.ClientSession() as session:
        tasks = []
        for language, url in MODELS_LINKS.items():
            task = asyncio.create_task(
                process_model(session, language, url),
                name=f"Task-{language}"
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

    log.info("--- Download Process Summary ---")
    success_count = 0
    fail_count = 0

    for i, result in enumerate(results):
        task_name = tasks[i].get_name()
        if isinstance(result, Exception):
            fail_count += 1
        elif result is None:
            success_count +=1

    log.info(f"Summary: {success_count} completed/skipped, {fail_count} failed.")


if __name__ == '__main__':
    log.info("Starting model download process...")
    if not Path("MODELS.json").exists() or not Path("MODEL_MAPPER.json").exists():
        log.error("MODELS.json or MODEL_MAPPER.json not found. Please create them.")
    else:
        asyncio.run(download_all_models())
    log.info("Model download process finished.")

"""
ALL VOICE MODEL CREDIT TO: https://alphacephei.com/vosk/models
FOR MORE VOICE MODEL, GO TO THAT WEBSITE ...????
ありがとう！！！
"""