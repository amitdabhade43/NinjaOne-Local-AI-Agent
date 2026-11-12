import uvicorn
from ninjaone_agent.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("ninjaone_agent.api:app", host=settings.host, port=settings.port, log_level=settings.log_level.lower(), reload=False)
