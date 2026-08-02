from pydantic import BaseSettings

class Settings(BaseSettings):
    version: str = "0.1.0"
    # Add other configuration variables here

settings = Settings()
