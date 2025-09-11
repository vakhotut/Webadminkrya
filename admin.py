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
import csv
import io
import time
import json
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

# Импорты для работы с кошельком и API
from ltc_hdwallet import ltc_wallet
from api import get_ltc_usd_rate, check_transaction_blockchair, check_transaction_sochain, check_transaction_nownodes

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
        
        # Инициализируем таблицы
        async with app['db_pool'].acquire() as conn:
            # Таблица для сгенерированных адресов
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS generated_addresses (
                    id SERIAL PRIMARY KEY,
                    address TEXT NOT NULL UNIQUE,
                    index INTEGER NOT NULL,
                    label TEXT,
                    balance REAL DEFAULT 0,
                    transaction_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица для статистики API эксплореров
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS explorer_api_stats (
                    id SERIAL PRIMARY KEY,
                    explorer_name TEXT NOT NULL UNIQUE,
                    total_requests INTEGER DEFAULT 0,
                    successful_requests INTEGER DEFAULT 0,
                    last_used TIMESTAMP,
                    daily_limit INTEGER DEFAULT 1000,
                    remaining_daily_requests INTEGER DEFAULT 1000,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Добавим начальные данные для API статистики
            explorers = ['Blockchair', 'Sochain', 'Nownodes']
            for explorer in explorers:
                await conn.execute('''
                    INSERT INTO explorer_api_stats (explorer_name, remaining_daily_requests)
                    VALUES ($1, 1000)
                    ON CONFLICT (explorer_name) DO NOTHING
                ''', explorer)
        
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

# Маршруты для системы оплаты
@routes.get('/admin/payment-system')
@aiohttp_jinja2.template('payment_system.html')
async def payment_system(request):
    db_pool = request.app['db_pool']
    
    try:
        # Получаем информацию о кошельке
        wallet_health = ltc_wallet.health_check()
        
        # Получаем последние сгенерированные адреса
        async with db_pool.acquire() as conn:
            addresses = await conn.fetch('''
                SELECT * FROM generated_addresses 
                ORDER BY created_at DESC 
                LIMIT 50
            ''')
            
            # Получаем статистику API
            api_stats = await conn.fetch('''
                SELECT explorer_name, total_requests, successful_requests, 
                       last_used, daily_limit, remaining_daily_requests
                FROM explorer_api_stats 
                ORDER BY explorer_name
            ''')
        
        # Получаем текущий курс LTC
        ltc_rate = await get_ltc_usd_rate()
        
        # Получаем настройки API
        api_config = {
            'blockchair_key': os.environ.get('BLOCKCHAIR_API_KEY', ''),
            'nownodes_key': os.environ.get('NOWNODES_API_KEY', '')
        }
        
        return {
            'wallet_health': wallet_health,
            'addresses': addresses,
            'api_stats': api_stats,
            'ltc_rate': ltc_rate,
            'api_config': api_config
        }
    except Exception as e:
        logger.error(f"Error in payment_system: {e}")
        return {
            'error': f'Ошибка загрузки данных: {e}',
            'wallet_health': {},
            'addresses': [],
            'api_stats': [],
            'ltc_rate': 0,
            'api_config': {}
        }

@routes.post('/admin/payment-system/generate-address')
async def generate_address(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        index = int(data['index']) if data.get('index') else None
        label = data.get('label', '')
        
        # Генерируем адрес
        address_data = ltc_wallet.generate_address(index=index)
        
        # Сохраняем в базу
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO generated_addresses (address, index, label)
                VALUES ($1, $2, $3)
            ''', address_data['address'], address_data['index'], label)
        
        return web.HTTPFound('/admin/payment-system')
    except Exception as e:
        logger.error(f"Error generating address: {e}")
        return web.HTTPFound('/admin/payment-system?error=1')

@routes.post('/admin/payment-system/api-config')
async def update_api_config(request):
    data = await request.post()
    
    try:
        # Сохраняем настройки API
        os.environ['BLOCKCHAIR_API_KEY'] = data.get('blockchair_key', '')
        os.environ['NOWNODES_API_KEY'] = data.get('nownodes_key', '')
        
        return web.HTTPFound('/admin/payment-system')
    except Exception as e:
        logger.error(f"Error updating API config: {e}")
        return web.HTTPFound('/admin/payment-system?error=1')

@routes.post('/admin/payment-system/create-backup')
async def create_backup(request):
    data = await request.post()
    
    try:
        password = data.get('password')
        password_confirm = data.get('password_confirm')
        
        if password != password_confirm:
            return web.HTTPFound('/admin/payment-system?error=password_mismatch')
        
        # Создаем резервную копию кошелька
        backup_path = f"wallet_backup_{int(time.time())}.enc"
        success = ltc_wallet.backup_wallet(backup_path)
        
        if success:
            # Возвращаем файл для скачивания
            response = web.StreamResponse()
            response.headers['Content-Type'] = 'application/octet-stream'
            response.headers['Content-Disposition'] = f'attachment; filename="{backup_path}"'
            
            await response.prepare(request)
            
            with open(backup_path, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    await response.write(chunk)
            
            # Удаляем временный файл
            os.remove(backup_path)
            
            await response.write_eof()
            return response
        else:
            return web.HTTPFound('/admin/payment-system?error=backup_failed')
    except Exception as e:
        logger.error(f"Error creating backup: {e}")
        return web.HTTPFound('/admin/payment-system?error=1')

@routes.post('/admin/payment-system/recover-wallet')
async def recover_wallet(request):
    data = await request.post()
    
    try:
        # Обработка восстановления кошелька из backup
        reader = await request.multipart()
        field = await reader.next()
        
        if field.name == 'backup_file':
            filename = field.filename
            backup_data = await field.read()
            
            # Сохраняем временный файл
            temp_path = f"temp_backup_{int(time.time())}.enc"
            with open(temp_path, 'wb') as f:
                f.write(backup_data)
            
            # Восстанавливаем кошелек
            # (здесь должна быть реализована логика восстановления)
            
            # Удаляем временный файл
            os.remove(temp_path)
            
        return web.HTTPFound('/admin/payment-system?success=recovered')
    except Exception as e:
        logger.error(f"Error recovering wallet: {e}")
        return web.HTTPFound('/admin/payment-system?error=1')

@routes.get('/admin/payment-system/export-addresses')
async def export_addresses(request):
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            addresses = await conn.fetch('SELECT * FROM generated_addresses ORDER BY created_at DESC')
        
        # Создаем CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Адрес', 'Индекс', 'Метка', 'Баланс', 'Транзакций', 'Дата создания'])
        
        for address in addresses:
            writer.writerow([
                address['address'],
                address['index'],
                address['label'] or '',
                address['balance'] or '0.0',
                address['transaction_count'] or '0',
                address['created_at'].strftime('%Y-%m-%d %H:%M')
            ])
        
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = 'attachment; filename="ltc_addresses.csv"'
        
        await response.prepare(request)
        await response.write(output.getvalue().encode('utf-8'))
        await response.write_eof()
        
        return response
    except Exception as e:
        logger.error(f"Error exporting addresses: {e}")
        return web.HTTPFound('/admin/payment-system?error=1')

@routes.get('/admin/payment-system/check-balance')
async def check_balance(request):
    db_pool = request.app['db_pool']
    address = request.query.get('address')
    
    try:
        # Проверяем баланс через все эксплореры
        balance = 0
        transaction_count = 0
        
        # Blockchair
        try:
            blockchair_data = await check_transaction_blockchair(address, 0)
            if blockchair_data:
                balance = max(balance, blockchair_data.get('balance', 0) / 100000000)  # Convert satoshi to LTC
                transaction_count = max(transaction_count, blockchair_data.get('transaction_count', 0))
        except:
            pass
        
        # Sochain
        try:
            sochain_data = await check_transaction_sochain(address, 0)
            if sochain_data:
                balance = max(balance, sochain_data.get('balance', 0))
                # Sochain не возвращает количество транзакций
        except:
            pass
        
        # Nownodes
        try:
            nownodes_data = await check_transaction_nownodes(address, 0)
            if nownodes_data:
                balance = max(balance, nownodes_data.get('balance', 0))
                transaction_count = max(transaction_count, nownodes_data.get('transaction_count', 0))
        except:
            pass
        
        # Обновляем информацию в базе
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE generated_addresses 
                SET balance = $1, transaction_count = $2 
                WHERE address = $3
            ''', balance, transaction_count, address)
        
        return web.json_response({
            'success': True,
            'balance': balance,
            'transaction_count': transaction_count
        })
    except Exception as e:
        logger.error(f"Error checking balance: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        })

@routes.post('/admin/payment-system/update-address/{address}')
async def update_address(request):
    address = request.match_info['address']
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        label = data.get('label', '')
        
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE generated_addresses 
                SET label = $1 
                WHERE address = $2
            ''', label, address)
        
        return web.HTTPFound('/admin/payment-system')
    except Exception as e:
        logger.error(f"Error updating address: {e}")
        return web.HTTPFound('/admin/payment-system?error=1')

@routes.get('/admin/payment-system/test-explorer')
async def test_explorer(request):
    explorer = request.query.get('explorer')
    test_address = "LVg2kJS4J6W6G2L6W6G2L6W6G2L6W6G2L6"  # Тестовый адрес
    
    try:
        start_time = time.time()
        
        if explorer == 'Blockchair':
            data = await check_transaction_blockchair(test_address, 0)
        elif explorer == 'Sochain':
            data = await check_transaction_sochain(test_address, 0)
        elif explorer == 'Nownodes':
            data = await check_transaction_nownodes(test_address, 0)
        else:
            return web.json_response({
                'success': False,
                'error': 'Unknown explorer'
            })
        
        response_time = int((time.time() - start_time) * 1000)
        
        if data:
            # Обновляем статистику API
            db_pool = request.app['db_pool']
            async with db_pool.acquire() as conn:
                await conn.execute('''
                    UPDATE explorer_api_stats 
                    SET total_requests = total_requests + 1,
                        successful_requests = successful_requests + 1,
                        last_used = NOW(),
                        remaining_daily_requests = GREATEST(0, remaining_daily_requests - 1),
                        updated_at = NOW()
                    WHERE explorer_name = $1
                ''', explorer)
            
            return web.json_response({
                'success': True,
                'response_time': response_time,
                'balance': data.get('balance', 0),
                'transaction_count': data.get('transaction_count', 0)
            })
        else:
            # Обновляем статистику API (только общее количество запросов)
            db_pool = request.app['db_pool']
            async with db_pool.acquire() as conn:
                await conn.execute('''
                    UPDATE explorer_api_stats 
                    SET total_requests = total_requests + 1,
                        last_used = NOW(),
                        remaining_daily_requests = GREATEST(0, remaining_daily_requests - 1),
                        updated_at = NOW()
                    WHERE explorer_name = $1
                ''', explorer)
            
            return web.json_response({
                'success': False,
                'error': 'No data returned'
            })
    except Exception as e:
        logger.error(f"Error testing explorer: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        })

@routes.post('/admin/payment-system/update-explorer/{explorer}')
async def update_explorer(request):
    explorer = request.match_info['explorer']
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        daily_limit = int(data.get('daily_limit', 1000))
        
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE explorer_api_stats 
                SET daily_limit = $1,
                    remaining_daily_requests = LEAST(remaining_daily_requests, $1),
                    updated_at = NOW()
                WHERE explorer_name = $2
            ''', daily_limit, explorer)
        
        return web.HTTPFound('/admin/payment-system')
    except Exception as e:
        logger.error(f"Error updating explorer: {e}")
        return web.HTTPFound('/admin/payment-system?error=1')

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

@routes.post('/admin/bot/cities/delete/{city_id}')
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

# Новые маршруты для бухгалтерии
@routes.get('/admin/accounting')
@aiohttp_jinja2.template('accounting.html')
async def accounting(request):
    db_pool = request.app['db_pool']
    
    # Получаем параметры фильтрации
    start_date = request.query.get('start_date')
    end_date = request.query.get('end_date')
    report_type = request.query.get('report_type', 'sales')
    
    try:
        async with db_pool.acquire() as conn:
            # Базовые запросы для разных типов отчетов
            if report_type == 'sales':
                # Отчет по продажам
                query = '''
                    SELECT p.*, u.username, u.first_name 
                    FROM purchases p 
                    LEFT JOIN users u ON p.user_id = u.user_id 
                    WHERE 1=1
                '''
                count_query = 'SELECT COUNT(*) FROM purchases p WHERE 1=1'
                
                if start_date:
                    query += f" AND p.purchase_time >= '{start_date}'"
                    count_query += f" AND p.purchase_time >= '{start_date}'"
                if end_date:
                    query += f" AND p.purchase_time <= '{end_date} 23:59:59'"
                    count_query += f" AND p.purchase_time <= '{end_date} 23:59:59'"
                
                query += ' ORDER BY p.purchase_time DESC'
                
                records = await conn.fetch(query)
                total_count = await conn.fetchval(count_query)
                
                # Статистика
                revenue_query = 'SELECT COALESCE(SUM(price), 0) FROM purchases p WHERE 1=1'
                if start_date:
                    revenue_query += f" AND p.purchase_time >= '{start_date}'"
                if end_date:
                    revenue_query += f" AND p.purchase_time <= '{end_date} 23:59:59'"
                
                total_revenue = await conn.fetchval(revenue_query)
                
                return {
                    'records': records,
                    'total_count': total_count,
                    'total_revenue': total_revenue,
                    'report_type': report_type,
                    'start_date': start_date,
                    'end_date': end_date
                }
                
            elif report_type == 'refunds':
                # Отчет по возвратам
                query = '''
                    SELECT t.*, u.username, u.first_name 
                    FROM transactions t 
                    LEFT JOIN users u ON t.user_id = u.user_id 
                    WHERE t.status = 'canceled'
                '''
                count_query = "SELECT COUNT(*) FROM transactions t WHERE t.status = 'canceled'"
                
                if start_date:
                    query += f" AND t.created_at >= '{start_date}'"
                    count_query += f" AND t.created_at >= '{start_date}'"
                if end_date:
                    query += f" AND t.created_at <= '{end_date} 23:59:59'"
                    count_query += f" AND t.created_at <= '{end_date} 23:59:59'"
                
                query += ' ORDER BY t.created_at DESC'
                
                records = await conn.fetch(query)
                total_count = await conn.fetchval(count_query)
                
                # Статистика
                refunds_query = "SELECT COALESCE(SUM(amount), 0) FROM transactions t WHERE t.status = 'canceled'"
                if start_date:
                    refunds_query += f" AND t.created_at >= '{start_date}'"
                if end_date:
                    refunds_query += f" AND t.created_at <= '{end_date} 23:59:59'"
                
                total_refunds = await conn.fetchval(refunds_query)
                
                return {
                    'records': records,
                    'total_count': total_count,
                    'total_refunds': total_refunds,
                    'report_type': report_type,
                    'start_date': start_date,
                    'end_date': end_date
                }
                
            elif report_type == 'transactions':
                # Отчет по всем транзакциям
                query = '''
                    SELECT t.*, u.username, u.first_name 
                    FROM transactions t 
                    LEFT JOIN users u ON t.user_id = u.user_id 
                    WHERE 1=1
                '''
                count_query = 'SELECT COUNT(*) FROM transactions t WHERE 1=1'
                
                if start_date:
                    query += f" AND t.created_at >= '{start_date}'"
                    count_query += f" AND t.created_at >= '{start_date}'"
                if end_date:
                    query += f" AND t.created_at <= '{end_date} 23:59:59'"
                    count_query += f" AND t.created_at <= '{end_date} 23:59:59'"
                
                query += ' ORDER BY t.created_at DESC'
                
                records = await conn.fetch(query)
                total_count = await conn.fetchval(count_query)
                
                # Статистика по статусам
                status_stats = {}
                for status in ['pending', 'paid', 'canceled']:
                    status_query = f"SELECT COALESCE(SUM(amount), 0) FROM transactions t WHERE t.status = '{status}'"
                    if start_date:
                    status_query += f" AND t.created_at >= '{start_date}'"
                    if end_date:
                        status_query += f" AND t.created_at <= '{end_date} 23:59:59'"
                    
                    status_amount = await conn.fetchval(status_query)
                    status_stats[status] = status_amount
                
                return {
                    'records': records,
                    'total_count': total_count,
                    'status_stats': status_stats,
                    'report_type': report_type,
                    'start_date': start_date,
                    'end_date': end_date
                }
    
    except Exception as e:
        logger.error(f"Error in accounting: {e}")
        return {
            'error': f'Ошибка загрузки данных: {e}',
            'records': [],
            'total_count': 0,
            'report_type': report_type,
            'start_date': start_date,
            'end_date': end_date
        }

@routes.get('/admin/accounting/export/excel')
async def export_accounting_excel(request):
    db_pool = request.app['db_pool']
    
    # Получаем параметры
    start_date = request.query.get('start_date')
    end_date = request.query.get('end_date')
    report_type = request.query.get('report_type', 'sales')
    
    try:
        async with db_pool.acquire() as conn:
            # Формируем запрос в зависимости от типа отчета
            if report_type == 'sales':
                query = '''
                    SELECT p.purchase_time, u.username, u.first_name, p.product, p.price, p.district, p.delivery_type
                    FROM purchases p 
                    LEFT JOIN users u ON p.user_id = u.user_id 
                    WHERE 1=1
                '''
                if start_date:
                    query += f" AND p.purchase_time >= '{start_date}'"
                if end_date:
                    query += f" AND p.purchase_time <= '{end_date} 23:59:59'"
                
                query += ' ORDER BY p.purchase_time DESC'
                
                records = await conn.fetch(query)
                
                # Создаем CSV в памяти
                output = io.StringIO()
                writer = csv.writer(output)
                
                # Заголовки
                writer.writerow(['Дата', 'Пользователь', 'Имя', 'Товар', 'Цена', 'Район', 'Тип доставки'])
                
                # Данные
                for record in records:
                    writer.writerow([
                        record['purchase_time'].strftime('%Y-%m-%d %H:%M') if record['purchase_time'] else '',
                        record['username'] or '',
                        record['first_name'] or '',
                        record['product'] or '',
                        record['price'] or 0,
                        record['district'] or '',
                        record['delivery_type'] or ''
                    ])
                
                # Подготавливаем ответ
                response = web.StreamResponse()
                response.headers['Content-Type'] = 'text/csv'
                response.headers['Content-Disposition'] = f'attachment; filename="sales_report_{start_date}_{end_date}.csv"'
                
                await response.prepare(request)
                await response.write(output.getvalue().encode('utf-8'))
                await response.write_eof()
                
                return response
                
            elif report_type == 'refunds':
                query = '''
                    SELECT t.created_at, u.username, u.first_name, t.amount, t.currency, t.status, t.invoice_uuid
                    FROM transactions t 
                    LEFT JOIN users u ON t.user_id = u.user_id 
                    WHERE t.status = 'canceled'
                '''
                if start_date:
                    query += f" AND t.created_at >= '{start_date}'"
                if end_date:
                    query += f" AND t.created_at <= '{end_date} 23:59:59'"
                
                query += ' ORDER BY t.created_at DESC'
                
                records = await conn.fetch(query)
                
                # Создаем CSV в памяти
                output = io.StringIO()
                writer = csv.writer(output)
                
                # Заголовки
                writer.writerow(['Дата', 'Пользователь', 'Имя', 'Сумма', 'Валюта', 'Статус', 'ID транзакции'])
                
                # Данные
                for record in records:
                    writer.writerow([
                        record['created_at'].strftime('%Y-%m-%d %H:%M') if record['created_at'] else '',
                        record['username'] or '',
                        record['first_name'] or '',
                        record['amount'] or 0,
                        record['currency'] or '',
                        record['status'] or '',
                        record['invoice_uuid'] or ''
                    ])
                
                # Подготавливаем ответ
                response = web.StreamResponse()
                response.headers['Content-Type'] = 'text/csv'
                response.headers['Content-Disposition'] = f'attachment; filename="refunds_report_{start_date}_{end_date}.csv"'
                
                await response.prepare(request)
                await response.write(output.getvalue().encode('utf-8'))
                await response.write_eof()
                
                return response
                
            elif report_type == 'transactions':
                query = '''
                    SELECT t.created_at, u.username, u.first_name, t.amount, t.currency, t.status, t.invoice_uuid
                    FROM transactions t 
                    LEFT JOIN users u ON t.user_id = u.user_id 
                    WHERE 1=1
                '''
                if start_date:
                    query += f" AND t.created_at >= '{start_date}'"
                if end_date:
                    query += f" AND t.created_at <= '{end_date} 23:59:59'"
                
                query += ' ORDER BY t.created_at DESC'
                
                records = await conn.fetch(query)
                
                # Создаем CSV в памяти
                output = io.StringIO()
                writer = csv.writer(output)
                
                # Заголовки
                writer.writerow(['Дата', 'Пользователь', 'Имя', 'Сумма', 'Валюта', 'Статус', 'ID транзакции'])
                
                # Данные
                for record in records:
                    writer.writerow([
                        record['created_at'].strftime('%Y-%m-%d %H:%M') if record['created_at'] else '',
                        record['username'] or '',
                        record['first_name'] or '',
                        record['amount'] or 0,
                        record['currency'] or '',
                        record['status'] or '',
                        record['invoice_uuid'] or ''
                    ])
                
                # Подготавливаем ответ
                response = web.StreamResponse()
                response.headers['Content-Type'] = 'text/csv'
                response.headers['Content-Disposition'] = f'attachment; filename="transactions_report_{start_date}_{end_date}.csv"'
                
                await response.prepare(request)
                await response.write(output.getvalue().encode('utf-8'))
                await response.write_eof()
                
                return response
    
    except Exception as e:
        logger.error(f"Error in export_accounting_excel: {e}")
        return web.Response(text=f"Ошибка экспорта: {e}", status=500)

@routes.get('/admin/accounting/export/pdf')
async def export_accounting_pdf(request):
    db_pool = request.app['db_pool']
    
    # Получаем параметры
    start_date = request.query.get('start_date')
    end_date = request.query.get('end_date')
    report_type = request.query.get('report_type', 'sales')
    
    try:
        async with db_pool.acquire() as conn:
            # Формируем запрос в зависимости от типа отчета
            if report_type == 'sales':
                query = '''
                    SELECT p.purchase_time, u.username, u.first_name, p.product, p.price, p.district, p.delivery_type
                    FROM purchases p 
                    LEFT JOIN users u ON p.user_id = u.user_id 
                    WHERE 1=1
                '''
                if start_date:
                    query += f" AND p.purchase_time >= '{start_date}'"
                if end_date:
                    query += f" AND p.purchase_time <= '{end_date} 23:59:59'"
                
                query += ' ORDER BY p.purchase_time DESC'
                
                records = await conn.fetch(query)
                
                # Создаем PDF в памяти
                buffer = io.BytesIO()
                doc = SimpleDocTemplate(buffer, pagesize=letter)
                elements = []
                
                # Заголовок
                styles = getSampleStyleSheet()
                title = Paragraph(f"Отчет по продажам: {start_date} - {end_date}", styles['Title'])
                elements.append(title)
                
                # Данные для таблицы
                data = [['Дата', 'Пользователь', 'Имя', 'Товар', 'Цена', 'Район', 'Тип доставки']]
                
                for record in records:
                    data.append([
                        record['purchase_time'].strftime('%Y-%m-%d %H:%M') if record['purchase_time'] else '',
                        record['username'] or '',
                        record['first_name'] or '',
                        record['product'] or '',
                        str(record['price'] or 0),
                        record['district'] or '',
                        record['delivery_type'] or ''
                    ])
                
                # Создаем таблицу
                table = Table(data)
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                    ('FONTSIZE', (0, 1), (-1, -1), 8),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black)
                ]))
                
                elements.append(table)
                
                # Строим PDF
                doc.build(elements)
                
                # Подготавливаем ответ
                response = web.StreamResponse()
                response.headers['Content-Type'] = 'application/pdf'
                response.headers['Content-Disposition'] = f'attachment; filename="sales_report_{start_date}_{end_date}.pdf"'
                
                await response.prepare(request)
                await response.write(buffer.getvalue())
                await response.write_eof()
                
                return response
                
            # Аналогично для других типов отчетов...
            # Код для refunds и transactions будет похожим
    
    except Exception as e:
        logger.error(f"Error in export_accounting_pdf: {e}")
        return web.Response(text=f"Ошибка экспорта: {e}", status=500)

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
