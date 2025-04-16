import asyncio
import json
import os
import sys
import logging
from vosk import Model
from model_downloader import download_all_models

class redirect_c_streams:
    """
    A context manager for temporarily redirecting C-level stdout and stderr
    by manipulating file descriptors. Useful for silencing C libraries.
    """
    def __init__(self, stdout_to=os.devnull, stderr_to=os.devnull):
        """
        Args:
            stdout_to (str): Path to redirect stdout to (default: os.devnull).
            stderr_to (str): Path to redirect stderr to (default: os.devnull).
        """
        self._stdout_to_path = stdout_to
        self._stderr_to_path = stderr_to

        self._stdout_to_f = None
        self._stderr_to_f = None

        self._orig_stdout_fd = None
        self._orig_stderr_fd = None

        self._target_stdout_fd = None
        self._target_stderr_fd = None

    def __enter__(self):
        sys.stdout.flush()
        sys.stderr.flush()

        self._orig_stdout_fd = os.dup(1)
        self._orig_stderr_fd = os.dup(2)

        try:
            self._stdout_to_f = open(self._stdout_to_path, 'wb')
            self._stderr_to_f = open(self._stderr_to_path, 'wb')

            self._target_stdout_fd = self._stdout_to_f.fileno()
            self._target_stderr_fd = self._stderr_to_f.fileno()

            os.dup2(self._target_stdout_fd, 1)
            os.dup2(self._target_stderr_fd, 2)
        except Exception as e:
            if self._orig_stdout_fd is not None: os.dup2(self._orig_stdout_fd, 1)
            if self._orig_stderr_fd is not None: os.dup2(self._orig_stderr_fd, 2)
            if self._stdout_to_f: self._stdout_to_f.close()
            if self._stderr_to_f: self._stderr_to_f.close()

            if self._orig_stdout_fd is not None: os.close(self._orig_stdout_fd)
            if self._orig_stderr_fd is not None: os.close(self._orig_stderr_fd)
            raise OSError(f"Failed to redirect C streams: {e}") from e

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            os.fsync(1)
        except OSError: pass
        try:
            os.fsync(2)
        except OSError: pass

        try:
            if self._orig_stdout_fd is not None:
                os.dup2(self._orig_stdout_fd, 1)
            if self._orig_stderr_fd is not None:
                os.dup2(self._orig_stderr_fd, 2)
        except OSError as e:
            logging.error(f"!!! Critical: Failed to restore original stdout/stderr: {e}", exc_info=True)

        finally:
            if self._orig_stdout_fd is not None:
                try:
                    os.close(self._orig_stdout_fd)
                except OSError as e:

                    logging.warning(f"Could not close saved original stdout fd ({self._orig_stdout_fd}): {e}")
            if self._orig_stderr_fd is not None:
                try:
                    os.close(self._orig_stderr_fd)
                except OSError as e:
                    logging.warning(f"Could not close saved original stderr fd ({self._orig_stderr_fd}): {e}")

            if self._stdout_to_f:
                try:
                    self._stdout_to_f.close()
                except OSError as e:
                    logging.warning(f"Could not close target stdout file ({self._stdout_to_path}): {e}")
            if self._stderr_to_f:
                try:
                    self._stderr_to_f.close()
                except OSError as e:
                    logging.warning(f"Could not close target stderr file ({self._stderr_to_path}): {e}")

        return False

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_BASE_DIR = os.path.join(PROJECT_ROOT, "Models")


with open(os.path.join(PROJECT_ROOT, "MODEL_LOADER.json"), "r", encoding="utf-8") as f:
    try:
        LANGUAGE_FOLDER_MAP = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error loading JSON file: {e}")
        LANGUAGE_FOLDER_MAP = {}


logger = logging.getLogger(__name__)

class ModelLoader:
    def __init__(self):
        self.models = {}

    @staticmethod
    def check_models_folder():
        possible_folders = os.listdir(MODELS_BASE_DIR)
        logger.info("Đang kiểm tra các model...")
        if len(possible_folders) == 0:
            logger.warning("Adu, ko có model nào hết, tải...")
            asyncio.run(download_all_models())

    def load_vosk_models(self) -> dict:
        """
        Quét thư mục MODELS_BASE_DIR, tải các model Vosk hợp lệ
        và trả về một dictionary {lang_code: vosk.Model}.
        """

        os.makedirs("Models", exist_ok=True)

        self.check_models_folder()


        loaded_models = {}
        logger.info(f"Scanning for models in: {MODELS_BASE_DIR}")

        if not os.path.isdir(MODELS_BASE_DIR):
            logger.error(f"Models directory not found at: {MODELS_BASE_DIR}. Cannot load any models.")
            return loaded_models

        possible_folders = os.listdir(MODELS_BASE_DIR)

        for folder_name in possible_folders:
            model_folder_path = os.path.join(MODELS_BASE_DIR, folder_name)

            if os.path.isdir(model_folder_path) and folder_name in LANGUAGE_FOLDER_MAP:
                lang_code = LANGUAGE_FOLDER_MAP[folder_name]
                abs_model_path = os.path.abspath(model_folder_path)

                if not os.path.exists(os.path.join(abs_model_path, 'am')):
                    logger.warning(f"Skipping folder '{folder_name}' - does not seem to contain Vosk model structure or mapping error, please check MODEL_MAPPER.json for sure.")
                    continue

                try:
                    logger.debug(f"Attempting to load model for lang='{lang_code}' (folder: {folder_name}) from '{abs_model_path}'...")

                    with redirect_c_streams():
                        model = Model(abs_model_path)

                    loaded_models[lang_code] = model
                    logger.info(f"[✅] Successfully loaded model for '{lang_code}'.") # Dùng markup

                except Exception as e:
                    logger.error(f"[❌] Failed to load Vosk model for lang='{lang_code}' from {abs_model_path}: {e}", exc_info=False) # Giảm traceback nếu muốn

            elif os.path.isdir(model_folder_path):
                logger.warning(f"Skipping folder '{folder_name}' - not found in LANGUAGE_FOLDER_MAP.")

        if not loaded_models:
            logger.warning("Warning: No Vosk models were loaded successfully!")
        else:
            logger.info(f"Finished loading models. Supported languages: {list(loaded_models.keys())}")

        self.models = loaded_models

        return self.models

    def get_model(self, lang_code) -> Model | None:
        """
        Trả về model Vosk cho mã ngôn ngữ đã cho.
        """
        return self.models.get(lang_code)

    def get_all(self):
        return self.models