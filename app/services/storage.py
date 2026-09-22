"""
storage.py - Saves and loads the original bill files.

On Azure: stored privately in Azure Blob Storage (container "bills").
Locally/tests: stored in a local "uploads" folder.
Files are named by their SHA-256 hash, so the same bill is never stored twice.
"""
import os
from functools import lru_cache
from pathlib import Path

from ..config import settings

EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}


@lru_cache
def _container():
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient.from_connection_string(settings.storage_connection_string)
    container = service.get_container_client(settings.storage_container)
    try:
        container.create_container()  # private by default
    except ResourceExistsError:
        pass
    return container


def _local_dir() -> Path:
    path = Path(os.getenv("LOCAL_UPLOAD_DIR", "uploads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_bill(data: bytes, file_hash: str, content_type: str) -> str:
    name = file_hash + EXTENSIONS.get(content_type, "")
    if settings.use_azure_storage:
        from azure.storage.blob import ContentSettings
        _container().upload_blob(
            name, data, overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
    else:
        (_local_dir() / name).write_bytes(data)
    return name


def load_bill(name: str) -> bytes:
    if settings.use_azure_storage:
        return _container().download_blob(name).readall()
    return (_local_dir() / name).read_bytes()
