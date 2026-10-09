"""Run with python -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1."""
import logging
from service.config import Settings
from service.app import create_app
settings=Settings.load()
logging.basicConfig(level=getattr(logging,settings.log_level,logging.INFO),
                    format='%(asctime)s %(levelname)s %(name)s %(message)s')
app=create_app(settings)
