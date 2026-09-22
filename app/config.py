"""
config.py - Reads all settings from environment variables.

Locally they come from a .env file. On Azure they come from
App Service > Configuration > Application settings.
Each value is read fresh every time, so tests can change them easily.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Azure AI Document Intelligence (reads the bill)
    @property
    def docintel_endpoint(self): return os.getenv("DOCINTEL_ENDPOINT", "")
    @property
    def docintel_key(self): return os.getenv("DOCINTEL_KEY", "")

    # Azure OpenAI (decides the category)
    @property
    def aoai_endpoint(self): return os.getenv("AOAI_ENDPOINT", "")
    @property
    def aoai_key(self): return os.getenv("AOAI_KEY", "")
    @property
    def aoai_deployment(self): return os.getenv("AOAI_DEPLOYMENT", "gpt-4o-mini")
    @property
    def aoai_api_version(self): return os.getenv("AOAI_API_VERSION", "2024-10-21")

    # Azure Blob Storage (keeps the bill images)
    @property
    def storage_connection_string(self): return os.getenv("STORAGE_CONNECTION_STRING", "")
    @property
    def storage_container(self): return os.getenv("STORAGE_CONTAINER", "bills")

    # Database (SQLite by default; on Azure we keep it in /home which is persistent)
    @property
    def database_url(self): return os.getenv("DATABASE_URL", "sqlite:///./reimbursements.db")

    # Helpers: is each Azure service configured? If not, we use a local fallback.
    @property
    def use_azure_ocr(self): return bool(self.docintel_endpoint and self.docintel_key)
    @property
    def use_azure_openai(self): return bool(self.aoai_endpoint and self.aoai_key)
    @property
    def use_azure_storage(self): return bool(self.storage_connection_string)


settings = Settings()
