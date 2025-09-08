import os
import logging
from aiohttp import web
import aiohttp_jinja2
import jinja2
import jwt
from datetime import datetime, timedelta
import asyncpg
from dotenv import load_dotenv
import ssl
import asyncio

# Загрузка переменных окружения
load_dotenv()

# Настройки
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'password')
JWT_SECRET = os.getenv('JWT_SECRET', 'your-secret-key')
DATABASE_URL = os.environ.get('DATABASE_URL')
PORT = int(os.environ.get('ADMIN_PORT', 5002))

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

# Middleware для проверки аутентификации
@web.middleware
async def auth_middleware(request, handler):
    if request.path.startswith('/admin/login') or request.path == '/admin':
        return await handler(request)
    
    token = request.cookies.get('auth_token')
    if not token:
        return web.HTTPFound('/admin/login')
    
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        request['user'] = payload
    except jwt.InvalidTokenError:
        response = web.HTTPFound('/admin/login')
        response.del_cookie('auth_token')
        return response
    
    return await handler(request)

async def init_db(app):
    try:
        # Для Render нам нужно использовать SSL соединение
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        # Подключаемся к базе данных
        app['db_pool'] = await asyncpg.create_pool(
            DATABASE_URL,
            ssl=ssl_context,
            min_size=1,
            max_size=10
        )
        logger.info("Database connection established successfully")
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        raise

async def close_db(app):
    if 'db_pool' in app:
        await app['db_pool'].close()
        logger.info("Database connection closed")

@routes.get('/admin')
async def admin_redirect(request):
    return web.HTTPFound('/admin/login')

@routes.get('/admin/login')
@aiohttp_jinja2.template('login.html')
async def login_form(request):
    error = request.query.get('error')
    return {'error': error}

@routes.post('/admin/login')
async def login(request):
    data = await request.post()
    username = data.get('username')
    password = data.get('password')
    
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = jwt.encode({
            'username': username,
            'exp': datetime.utcnow() + timedelta(hours=24)
        }, JWT_SECRET, algorithm='HS256')
        
        response = web.HTTPFound('/admin/dashboard')
        response.set_cookie('auth_token', token, httponly=True, max_age=86400)
        return response
    else:
        return web.HTTPFound('/admin/login?error=1')

@routes.get('/admin/logout')
async def logout(request):
    response = web.HTTPFound('/admin/login')
    response.del_cookie('auth_token')
    return response

@routes.get('/admin/dashboard')
@aiohttp_jinja2.template('dashboard.html')
async def dashboard(request):
    db_pool = request.app['db_pool']
    
    async with db_pool.acquire() as conn:
        # Статистика пользователей
        total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
        today_users = await conn.fetchval(
            'SELECT COUNT(*) FROM users WHERE created_at >= $1',
            datetime.now().date()
        )
        
        # Статистика заказов
        total_orders = await conn.fetchval('SELECT COUNT(*) FROM purchases')
        today_orders = await conn.fetchval(
            'SELECT COUNT(*) FROM purchases WHERE purchase_time >= $1',
            datetime.now().date()
        )
        
        # Статистика транзакций
        total_transactions = await conn.fetchval('SELECT COUNT(*) FROM transactions')
        pending_transactions = await conn.fetchval(
            'SELECT COUNT(*) FROM transactions WHERE status = $1',
            'pending'
        )
        
        # Общая выручка
        total_revenue = await conn.fetchval(
            'SELECT COALESCE(SUM(price), 0) FROM purchases'
        )
        
        today_revenue = await conn.fetchval(
            'SELECT COALESCE(SUM(price), 0) FROM purchases WHERE purchase_time >= $1',
            datetime.now().date()
        )
        
        # Последние заказы
        recent_orders = await conn.fetch('''
            SELECT p.*, u.username, u.first_name 
            FROM purchases p 
            LEFT JOIN users u ON p.user_id = u.user_id 
            ORDER BY p.purchase_time DESC 
            LIMIT 10
        ''')
        
        # Последние транзакции
        recent_transactions = await conn.fetch('''
            SELECT t.*, u.username, u.first_name 
            FROM transactions t 
            LEFT JOIN users u ON t.user_id = u.user_id 
            ORDER BY t.created_at DESC 
            LIMIT 10
        ''')
        
        # Активные пользователи
        active_users = await conn.fetch('''
            SELECT user_id, username, first_name, last_purchase,
                   purchase_count, balance, created_at
            FROM users 
            ORDER BY last_purchase DESC NULLS LAST
            LIMIT 10
        ''')
    
    return {
        'total_users': total_users,
        'today_users': today_users,
        'total_orders': total_orders,
        'today_orders': today_orders,
        'total_transactions': total_transactions,
        'pending_transactions': pending_transactions,
        'total_revenue': total_revenue,
        'today_revenue': today_revenue,
        'recent_orders': recent_orders,
        'recent_transactions': recent_transactions,
        'active_users': active_users
    }

@routes.get('/admin/users')
@aiohttp_jinja2.template('users.html')
async def users_list(request):
    db_pool = request.app['db_pool']
    page = int(request.query.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    async with db_pool.acquire() as conn:
        users = await conn.fetch('''
            SELECT * FROM users 
            ORDER BY created_at DESC 
            LIMIT $1 OFFSET $2
        ''', per_page, offset)
        
        total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
    
    return {
        'users': users,
        'page': page,
        'total_pages': (total_users + per_page - 1) // per_page
    }

@routes.get('/admin/orders')
@aiohttp_jinja2.template('orders.html')
async def orders_list(request):
    db_pool = request.app['db_pool']
    page = int(request.query.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    async with db_pool.acquire() as conn:
        orders = await conn.fetch('''
            SELECT p.*, u.username, u.first_name 
            FROM purchases p 
            LEFT JOIN users u ON p.user_id = u.user_id 
            ORDER BY p.purchase_time DESC 
            LIMIT $1 OFFSET $2
        ''', per_page, offset)
        
        total_orders = await conn.fetchval('SELECT COUNT(*) FROM purchases')
    
    return {
        'orders': orders,
        'page': page,
        'total_pages': (total_orders + per_page - 1) // per_page
    }

@routes.get('/admin/transactions')
@aiohttp_jinja2.template('transactions.html')
async def transactions_list(request):
    db_pool = request.app['db_pool']
    page = int(request.query.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    async with db_pool.acquire() as conn:
        transactions = await conn.fetch('''
            SELECT t.*, u.username, u.first_name 
            FROM transactions t 
            LEFT JOIN users u ON t.user_id = u.user_id 
            ORDER BY t.created_at DESC 
            LIMIT $1 OFFSET $2
        ''', per_page, offset)
        
        total_transactions = await conn.fetchval('SELECT COUNT(*) FROM transactions')
    
    return {
        'transactions': transactions,
        'page': page,
        'total_pages': (total_transactions + per_page - 1) // per_page
    }

@routes.post('/admin/transactions/{transaction_id}/cancel')
async def cancel_transaction(request):
    transaction_id = int(request.match_info['transaction_id'])
    db_pool = request.app['db_pool']
    
    async with db_pool.acquire() as conn:
        transaction = await conn.fetchrow(
            'SELECT * FROM transactions WHERE id = $1',
            transaction_id
        )
        
        if transaction and transaction['status'] == 'pending':
            # Обновляем статус транзакции
            await conn.execute(
                'UPDATE transactions SET status = $1 WHERE id = $2',
                'canceled', transaction_id
            )
    
    return web.HTTPFound('/admin/transactions')

def create_admin_app():
    app = web.Application(middlewares=[auth_middleware])
    
    # Настройка шаблонизатора
    aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('templates'))
    
    app.add_routes(routes)
    app.on_startup.append(init_db)
    app.on_cleanup.append(close_db)
    
    return app

async def main():
    # Проверяем, что DATABASE_URL установлена
    if not DATABASE_URL:
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
