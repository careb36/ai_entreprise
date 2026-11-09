import os

class Settings:
    """
    Environment-driven configuration with safe defaults (STD-OPS-1).
    """
    MAX_CORRECTION_ATTEMPTS: int = int(os.getenv("MAX_CORRECTION_ATTEMPTS", "3"))

settings = Settings()
