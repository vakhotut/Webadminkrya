from aiohttp import web
import aiohttp_jinja2
import jinja2
from datetime import datetime, timedelta
import asyncpg
import os
from aiohttp_session import setup, get_session, session_middleware
from aiohttp_session.cookie_storage import EncryptedCookieStorage
import base64

# Настройки
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://user:pass@localhost/dbname')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')
ADMIN_SECRET_KEY = os.environ.get('ADMIN_SECRET_KEY', 'your-secret-key-here')

async def create_admin_app():
    app = web.Application()
    aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('templates/admin'))
    
    # Настройка сессий
    secret_key = base64.urlsafe_b64decode(ADMIN_SECRET_KEY.encode())
    setup(app, EncryptedCookieStorage(secret_key))
    
    # Подключение к БД
    app['db'] = await asyncpg.create_pool(DATABASE_URL, ssl='require')
    
    # Добавляем маршруты
    app.router.add_get('/admin/login', admin_login)
    app.router.add_post('/admin/login', admin_login_post)
    app.router.add_get('/admin/logout', admin_logout)
    app.router.add_get('/admin', admin_dashboard)
    app.router.add_get('/admin/users', admin_users)
    app.router.add_get('/admin/transactions', admin_transactions)
    app.router.add_get('/admin/orders', admin_orders)
    
    return app

async def admin_login(request):
    session = await get_session(request)
    if session.get('admin_logged_in'):
        return web.HTTPFound('/admin')
    return aiohttp_jinja2.render_template('login.html', request, {})

async def admin_login_post(request):
    session = await get_session(request)
    data = await request.post()
    username = data.get('username')
    password = data.get('password')
    
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session['admin_logged_in'] = True
        return web.HTTPFound('/admin')
    else:
        return aiohttp_jinja2.render_template('login.html', request, {'error': 'Неверные учетные данные'})

async def admin_logout(request):
    session = await get_session(request)
    session.pop('admin_logged_in', None)
    return web.HTTPFound('/admin/login')

@aiohttp_jinja2.template('dashboard.html')
async def admin_dashboard(request):
    session = await get_session(request)
    if not session.get('admin_logged_in'):
        return web.HTTPFound('/admin/login')
    
    db = request.app['db']
    async with db.acquire() as conn:
        total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
        total_orders = await conn.fetchval('SELECT COUNT(*) FROM purchases')
        total_revenue = await conn.fetchval('SELECT COALESCE(SUM(price), 0) FROM purchases WHERE status = $1', 'completed')
        
        week_ago = datetime.now() - timedelta(days=7)
        weekly_users = await conn.fetchval('SELECT COUNT(*) FROM users WHERE created_at >= $1', week_ago)
        weekly_orders = await conn.fetchval('SELECT COUNT(*) FROM purchases WHERE purchase_time >= $1', week_ago)
        weekly_revenue = await conn.fetchval('SELECT COALESCE(SUM(price), 0) FROM purchases WHERE purchase_time >= $1 AND status = $2', week_ago, 'completed')
        
        recent_users = await conn.fetch('SELECT * FROM users ORDER BY created_at DESC LIMIT 5')
        recent_transactions = await conn.fetch('''
            SELECT t.*, u.username, u.first_name 
            FROM transactions t 
            LEFT JOIN users u ON t.user_id = u.user_id 
            ORDER BY created_at DESC LIMIT 5
        ''')
    
    return {
        'stats': {
            'total_users': total_users,
            'total_orders': total_orders,
            'total_revenue': total_revenue,
            'weekly_users': weekly_users,
            'weekly_orders': weekly_orders,
            'weekly_revenue': weekly_revenue
        },
        'recent_users': recent_users,
        'recent_transactions': recent_transactions
    }

@aiohttp_jinja2.template('users.html')
async def admin_users(request):
    session = await get_session(request)
    if not session.get('admin_logged_in'):
        return web.HTTPFound('/admin/login')
    
    db = request.app['db']
    async with db.acquire() as conn:
        users = await conn.fetch('SELECT * FROM users ORDER BY created_at DESC')
    
    return {'users': users}

@aiohttp_jinja2.template('transactions.html')
async def admin_transactions(request):
    session = await get_session(request)
    if not session.get('admin_logged_in'):
        return web.HTTPFound('/admin/login')
    
    db = request.app['db']
    async with db.acquire() as conn:
        transactions = await conn.fetch('''
            SELECT t.*, u.username, u.first_name 
            FROM transactions t 
            LEFT JOIN users u ON t.user_id = u.user_id 
            ORDER BY created_at DESC
        ''')
    
    return {'transactions': transactions}

@aiohttp_jinja2.template('orders.html')
async def admin_orders(request):
    session = await get_session(request)
    if not session.get('admin_logged_in'):
        return web.HTTPFound('/admin/login')
    
    db = request.app['db']
    async with db.acquire() as conn:
        purchases = await conn.fetch('''
            SELECT p.*, u.username, u.first_name 
            FROM purchases p 
            LEFT JOIN users u ON p.user_id = u.user_id 
            ORDER BY purchase_time DESC
        ''')
    
    return {'purchases': purchases}

async def main():
    app = await create_admin_app()
    port = int(os.environ.get('ADMIN_PORT', 5002))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Admin panel started on port {port}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
