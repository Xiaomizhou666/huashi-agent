"""启动“化实”FastAPI Web 前端与 API。"""

from dotenv import load_dotenv

from huashi.api import create_app

load_dotenv()
app = create_app()
