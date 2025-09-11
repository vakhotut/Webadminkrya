import os
import logging
from aiohttp import web
import aiohttp_jinja2
import jinja2
from dotenv import load_dotenv
import asyncio

from database import init_db, close_db
from auth import auth_middleware
from users import users_routes
from orders import orders_routes
from transactions import transactions_routes
from payment_system import payment_system_routes
from products import products_routes
from bot_management import bot_management_routes
from accounting import accounting_routes

# Загрузка переменных окружения
load_dotenv()

# Настройки
PORT = int(os.environ.get('ADMIN_PORT', 5002))

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_admin_app():
    app = web.Application(middlewares=[auth_middleware])
    
    # Настройка шаблонизатора
    aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('templates'))
    
    # Добавление маршрутов из всех модулей
    app.add_routes(users_routes)
    app.add_routes(orders_routes)
    app.add_routes(transactions_routes)
    app.add_routes(payment_system_routes)
    app.add_routes(products_routes)
    app.add_routes(bot_management_routes)
    app.add_routes(accounting_routes)
    
    app.on_startup.append(init_db)
    app.on_cleanup.append(close_db)
    
    return app

async def main():
    # Проверяем, что DATABASE_URL установлена
    if not os.environ.get('DATABASE_URL'):
        logger.error("DATABASE_URL environment variable is not set")
        return
    
    app = create_admin_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"Admin panel started on port {PORT}")
    
    # Бесконечное ожидание
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
