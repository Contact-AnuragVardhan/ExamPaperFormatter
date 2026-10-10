"""Run with python -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1."""
from runtime_logging import configure_logging
from service.config import Settings
from service.app import create_app
settings=Settings.load()
configure_logging(settings.log_level)
app=create_app(settings)
