import os
import logging
from aiohttp import web
import aiohttp_jinja2
import jinja2
import jwt
from datetime import datetime, timedelta, timezone
import asyncpg
from dotenv import load_dotenv
import ssl
import asyncio
import uuid

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
            'exp': datetime.now(timezone.utc) + timedelta(hours=24)
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
    
    try:
        async with db_pool.acquire() as conn:
            # Проверяем существование таблиц
            users_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'users')"
            )
            
            if not users_table_exists:
                return {
                    'error': 'Таблицы базы данных не созданы. Запустите сначала основного бота.',
                    'total_users': 0,
                    'today_users': 0,
                    'total_orders': 0,
                    'today_orders': 0,
                    'total_transactions': 0,
                    'pending_transactions': 0,
                    'total_revenue': 0,
                    'today_revenue': 0,
                    'recent_orders': [],
                    'recent_transactions': [],
                    'active_users': []
                }
            
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
            
            # Активные пользователи (используем created_at вместо last_purchase)
            active_users = await conn.fetch('''
                SELECT user_id, username, first_name, created_at,
                       purchase_count, balance
                FROM users 
                ORDER BY created_at DESC
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
    except Exception as e:
        logger.error(f"Error in dashboard: {e}")
        return {
            'error': f'Ошибка загрузки данных: {e}',
            'total_users': 0,
            'today_users': 0,
            'total_orders': 0,
            'today_orders': 0,
            'total_transactions': 0,
            'pending_transactions': 0,
            'total_revenue': 0,
            'today_revenue': 0,
            'recent_orders': [],
            'recent_transactions': [],
            'active_users': []
        }

@routes.get('/admin/users')
@aiohttp_jinja2.template('users.html')
async def users_list(request):
    db_pool = request.app['db_pool']
    page = int(request.query.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    try:
        async with db_pool.acquire() as conn:
            # Проверяем существование таблицы users
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'users')"
            )
            
            if not table_exists:
                return {
                    'error': 'Таблица пользователей не создана. Запустите сначала основного бота.',
                    'users': [],
                    'page': 1,
                    'total_pages': 0
                }
            
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
    except Exception as e:
        logger.error(f"Error in users_list: {e}")
        return {
            'error': f'Ошибка загрузки пользователей: {e}',
            'users': [],
            'page': 1,
            'total_pages': 0
        }

@routes.get('/admin/orders')
@aiohttp_jinja2.template('orders.html')
async def orders_list(request):
    db_pool = request.app['db_pool']
    page = int(request.query.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    try:
        async with db_pool.acquire() as conn:
            # Проверяем существование таблицы purchases
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'purchases')"
            )
            
            if not table_exists:
                return {
                    'error': 'Таблица заказов не создана. Запустите сначала основного бота.',
                    'orders': [],
                    'page': 1,
                    'total_pages': 0
                }
            
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
    except Exception as e:
        logger.error(f"Error in orders_list: {e}")
        return {
            'error': f'Ошибка загрузки заказов: {e}',
            'orders': [],
            'page': 1,
            'total_pages': 0
        }

@routes.get('/admin/transactions')
@aiohttp_jinja2.template('transactions.html')
async def transactions_list(request):
    db_pool = request.app['db_pool']
    page = int(request.query.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    try:
        async with db_pool.acquire() as conn:
            # Проверяем существование таблицы transactions
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'transactions')"
            )
            
            if not table_exists:
                return {
                    'error': 'Таблица транзакций не создана. Запустите сначала основного бота.',
                    'transactions': [],
                    'page': 1,
                    'total_pages': 0
                }
            
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
    except Exception as e:
        logger.error(f"Error in transactions_list: {e}")
        return {
            'error': f'Ошибка загрузки транзакций: {e}',
            'transactions': [],
            'page': 1,
            'total_pages': 0
        }

@routes.post('/admin/transactions/{transaction_id}/cancel')
async def cancel_transaction(request):
    transaction_id = int(request.match_info['transaction_id'])
    db_pool = request.app['db_pool']
    
    try:
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
    except Exception as e:
        logger.error(f"Error in cancel_transaction: {e}")
        return web.HTTPFound('/admin/transactions?error=1')

# Новые маршруты для управления товарами
@routes.get('/admin/products')
@aiohttp_jinja2.template('products.html')
async def products_list(request):
    db_pool = request.app['db_pool']
    page = int(request.query.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    try:
        async with db_pool.acquire() as conn:
            # Проверяем существование таблицы products
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'products')"
            )
            
            if not table_exists:
                return {
                    'error': 'Таблица товаров не создана. Запустите сначала основного бота.',
                    'products': [],
                    'cities': [],
                    'categories': [],
                    'districts': [],
                    'delivery_types': [],
                    'page': 1,
                    'total_pages': 0
                }
            
            products = await conn.fetch('''
                SELECT p.*, c.name as city_name, cat.name as category_name,
                       d.name as district_name, dt.name as delivery_type_name
                FROM products p
                LEFT JOIN cities c ON p.city_id = c.id
                LEFT JOIN categories cat ON p.category_id = cat.id
                LEFT JOIN districts d ON p.district_id = d.id
                LEFT JOIN delivery_types dt ON p.delivery_type_id = dt.id
                ORDER BY p.id DESC  -- Исправлено: было p.created_at DESC
                LIMIT $1 OFFSET $2
            ''', per_page, offset)
            
            total_products = await conn.fetchval('SELECT COUNT(*) FROM products')
            
            # Проверяем существование связанных таблиц
            cities_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'cities')"
            )
            categories_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'categories')"
            )
            districts_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'districts')"
            )
            delivery_types_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'delivery_types')"
            )
            
            cities = []
            categories = []
            districts = []
            delivery_types = []
            
            if cities_table_exists:
                cities = await conn.fetch('SELECT * FROM cities ORDER BY name')
            
            if categories_table_exists:
                categories = await conn.fetch('SELECT * FROM categories ORDER BY name')
            
            if districts_table_exists:
                districts = await conn.fetch('SELECT * FROM districts ORDER BY name')
            
            if delivery_types_table_exists:
                delivery_types = await conn.fetch('SELECT * FROM delivery_types ORDER BY name')
        
        return {
            'products': products,
            'cities': cities,
            'categories': categories,
            'districts': districts,
            'delivery_types': delivery_types,
            'page': page,
            'total_pages': (total_products + per_page - 1) // per_page
        }
    except Exception as e:
        logger.error(f"Error in products_list: {e}")
        return {
            'error': f'Ошибка загрузки товаров: {e}',
            'products': [],
            'cities': [],
            'categories': [],
            'districts': [],
            'delivery_types': [],
            'page': 1,
            'total_pages': 0
        }

@routes.post('/admin/products/add')
async def add_product(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        # Генерируем уникальный идентификатор товара
        product_uuid = str(uuid.uuid4())
        
        async with db_pool.acquire() as conn:
            # Если выбрана новая категория, создаем ее
            if data['category_id'] == 'new':
                category_id = await conn.fetchval(
                    'INSERT INTO categories (name) VALUES ($1) RETURNING id',
                    data['new_category']
                )
            else:
                category_id = int(data['category_id'])
            
            # Если выбран новый город, создаем его
            if data['city_id'] == 'new':
                city_id = await conn.fetchval(
                    'INSERT INTO cities (name) VALUES ($1) RETURNING id',
                    data['new_city']
                )
            else:
                city_id = int(data['city_id'])
            
            # Если выбран новый район, создаем его
            if data['district_id'] == 'new':
                district_id = await conn.fetchval(
                    'INSERT INTO districts (name, city_id) VALUES ($1, $2) RETURNING id',
                    data['new_district'], city_id
                )
            else:
                district_id = int(data['district_id'])
            
            # Если выбран новый тип доставки, создаем его
            if data['delivery_type_id'] == 'new':
                delivery_type_id = await conn.fetchval(
                    'INSERT INTO delivery_types (name) VALUES ($1) RETURNING id',
                    data['new_delivery_type']
                )
            else:
                delivery_type_id = int(data['delivery_type_id'])
            
            # Добавляем товар
            await conn.execute('''
                INSERT INTO products 
                (uuid, name, description, price, image_url, category_id, city_id, district_id, delivery_type_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ''', product_uuid, data['name'], data['description'], float(data['price']), 
               data['image_url'], category_id, city_id, district_id, delivery_type_id)
        
        return web.HTTPFound('/admin/products')
    except Exception as e:
        logger.error(f"Error in add_product: {e}")
        return web.HTTPFound('/admin/products?error=1')

@routes.post('/admin/products/update/{product_id}')
async def update_product(request):
    product_id = int(request.match_info['product_id'])
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE products 
                SET name = $1, description = $2, price = $3, image_url = $4,
                    category_id = $5, city_id = $6, district_id = $7, delivery_type_id = $8,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = $9
            ''', data['name'], data['description'], float(data['price']), data['image_url'],
               int(data['category_id']), int(data['city_id']), int(data['district_id']), 
               int(data['delivery_type_id']), product_id)
        
        return web.HTTPFound('/admin/products')
    except Exception as e:
        logger.error(f"Error in update_product: {e}")
        return web.HTTPFound('/admin/products?error=1')

@routes.post('/admin/products/delete/{product_id}')
async def delete_product(request):
    product_id = int(request.match_info['product_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM products WHERE id = $1', product_id)
        
        return web.HTTPFound('/admin/products')
    except Exception as e:
        logger.error(f"Error in delete_product: {e}")
        return web.HTTPFound('/admin/products?error=1')

# Новые маршруты для управления ботом
@routes.get('/admin/bot-management')
@aiohttp_jinja2.template('bot_management.html')
async def bot_management(request):
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            # Проверяем существование таблиц
            texts_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'texts')"
            )
            cities_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'cities')"
            )
            
            if not texts_table_exists or not cities_table_exists:
                return {
                    'error': 'Таблицы бота не созданы. Запустите сначала основного бота.',
                    'texts': [],
                    'languages': [],
                    'c cities': [],
                    'districts': [],
                    'products': [],
                    'delivery_types': []
                }
            
            # Загружаем данные для всех разделов
            texts = await conn.fetch('SELECT * FROM texts ORDER BY lang, key')
            languages = await conn.fetch('SELECT DISTINCT lang FROM texts ORDER BY lang')
            cities = await conn.fetch('SELECT * FROM cities ORDER BY name')
            
            districts = await conn.fetch('''
                SELECT d.*, c.name as city_name 
                FROM districts d 
                JOIN cities c ON d.city_id = c.id 
                ORDER BY c.name, d.name
            ''')
            
            products = await conn.fetch('''
                SELECT p.*, c.name as city_name 
                FROM products p 
                JOIN cities c ON p.city_id = c.id 
                ORDER BY c.name, p.name
            ''')
            
            delivery_types = await conn.fetch('SELECT * FROM delivery_types ORDER BY name')
        
        return {
            'texts': texts,
            'languages': [lang['lang'] for lang in languages],
            'cities': cities,
            'districts': districts,
            'products': products,
            'delivery_types': delivery_types
        }
    except Exception as e:
        logger.error(f"Error in bot_management: {e}")
        return {
            'error': f'Ошибка загрузки данных бота: {e}',
            'texts': [],
            'languages': [],
            'cities': [],
            'districts': [],
            'products': [],
            'delivery_types': []
        }

@routes.post('/admin/bot/texts/update')
async def update_text(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'UPDATE texts SET value = $1 WHERE id = $2',
                data['value'], int(data['id'])
            )
        
        return web.HTTPFound('/admin/bot-management#texts')
    except Exception as e:
        logger.error(f"Error in update_text: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#texts')

@routes.post('/admin/bot/texts/add')
async def add_text(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO texts (lang, key, value) VALUES ($1, $2, $3)',
                data['lang'], data['key'], data['value']
            )
        
        return web.HTTPFound('/admin/bot-management#texts')
    except Exception as e:
        logger.error(f"Error in add_text: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#texts')

@routes.post('/admin/bot/texts/delete/{text_id}')
async def delete_text(request):
    text_id = int(request.match_info['text_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM texts WHERE id = $1', text_id)
        
        return web.HTTPFound('/admin/bot-management#texts')
    except Exception as e:
        logger.error(f"Error in delete_text: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#texts')

@routes.post('/admin/bot/cities/update')
async def update_city(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'UPDATE cities SET name = $1 WHERE id = $2',
                data['name'], int(data['id'])
            )
        
        return web.HTTPFound('/admin/bot-management#cities')
    except Exception as e:
        logger.error(f"Error in update_city: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#cities')

@routes.post('/admin/bot/cities/add')
async def add_city(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO cities (name) VALUES ($1)',
                data['name']
            )
        
        return web.HTTPFound('/admin/bot-management#cities')
    except Exception as e:
        logger.error(f"Error in add_city: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#cities')

@routes.post('/admin/bot/cities/delete/{city_id')
async def delete_city(request):
    city_id = int(request.match_info['city_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM cities WHERE id = $1', city_id)
        
        return web.HTTPFound('/admin/bot-management#cities')
    except Exception as e:
        logger.error(f"Error in delete_city: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#cities')

@routes.post('/admin/bot/districts/update')
async def update_district(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'UPDATE districts SET name = $1, city_id = $2 WHERE id = $3',
                data['name'], int(data['city_id']), int(data['id'])
            )
        
        return web.HTTPFound('/admin/bot-management#districts')
    except Exception as e:
        logger.error(f"Error in update_district: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#districts')

@routes.post('/admin/bot/districts/add')
async def add_district(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO districts (name, city_id) VALUES ($1, $2)',
                data['name'], int(data['city_id'])
            )
        
        return web.HTTPFound('/admin/bot-management#districts')
    except Exception as e:
        logger.error(f"Error in add_district: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#districts')

@routes.post('/admin/bot/districts/delete/{district_id}')
async def delete_district(request):
    district_id = int(request.match_info['district_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM districts WHERE id = $1', district_id)
        
        return web.HTTPFound('/admin/bot-management#districts')
    except Exception as e:
        logger.error(f"Error in delete_district: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#districts')

@routes.post('/admin/bot/products/update')
async def update_product_bot(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'UPDATE products SET name = $1, price = $2, image_url = $3, city_id = $4 WHERE id = $5',
                data['name'], float(data['price']), data['image_url'], int(data['city_id']), int(data['id'])
            )
        
        return web.HTTPFound('/admin/bot-management#products')
    except Exception as e:
        logger.error(f"Error in update_product_bot: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#products')

@routes.post('/admin/bot/products/add')
async def add_product_bot(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO products (name, price, image_url, city_id) VALUES ($1, $2, $3, $4)',
                data['name'], float(data['price']), data['image_url'], int(data['city_id'])
            )
        
        return web.HTTPFound('/admin/bot-management#products')
    except Exception as e:
        logger.error(f"Error in add_product_bot: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#products')

@routes.post('/admin/bot/products/delete/{product_id}')
async def delete_product_bot(request):
    product_id = int(request.match_info['product_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM products WHERE id = $1', product_id)
        
        return web.HTTPFound('/admin/bot-management#products')
    except Exception as e:
        logger.error(f"Error in delete_product_bot: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#products')

@routes.post('/admin/bot/delivery-types/update')
async def update_delivery_type(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'UPDATE delivery_types SET name = $1 WHERE id = $2',
                data['name'], int(data['id'])
            )
        
        return web.HTTPFound('/admin/bot-management#delivery')
    except Exception as e:
        logger.error(f"Error in update_delivery_type: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#delivery')

@routes.post('/admin/bot/delivery-types/add')
async def add_delivery_type(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO delivery_types (name) VALUES ($1)',
                data['name']
            )
        
        return web.HTTPFound('/admin/bot-management#delivery')
    except Exception as e:
        logger.error(f"Error in add_delivery_type: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#delivery')

@routes.post('/admin/bot/delivery-types/delete/{type_id}')
async def delete_delivery_type(request):
    type_id = int(request.match_info['type_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM delivery_types WHERE id = $1', type_id)
        
        return web.HTTPFound('/admin/bot-management#delivery')
    except Exception as e:
        logger.error(f"Error in delete_delivery_type: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#delivery')

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
