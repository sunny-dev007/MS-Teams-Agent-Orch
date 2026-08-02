from pydantic import BaseSettings

class Settings(BaseSettings):
    VERSION: str = "0.2.0"
    # other settings...

settings = Settings()
