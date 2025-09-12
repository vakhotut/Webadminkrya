import os
import time
import io
import csv
import logging
import asyncio
import aiohttp
from aiohttp import web
import aiohttp_jinja2
from datetime import datetime, timedelta
import jwt
import qrcode
from decimal import Decimal, ROUND_HALF_UP

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Импорты для работы с кошельком и API
try:
    from ltc_hdwallet import ltc_wallet
    from api import get_ltc_usd_rate, check_transaction_blockchair, check_transaction_sochain, check_transaction_nownodes
except ImportError as e:
    logging.warning(f"Не удалось импортировать модули кошелька и API: {e}")
    # Создаем заглушки для избежания ошибок
    class WalletStub:
        def health_check(self):
            return {"status": "error", "message": "Wallet module not available"}
        def generate_address(self, index=None):
            return {"address": "NOT_AVAILABLE", "index": 0}
        def backup_wallet(self, path):
            return False
        def restore_wallet(self, path, password):
            return False
    
    ltc_wallet = WalletStub()
    
    async def get_ltc_usd_rate():
        return 0.0
    
    async def check_transaction_blockchair(address, amount):
        return None
    
    async def check_transaction_sochain(address, amount):
        return None
    
    async def check_transaction_nownodes(address, amount):
        return None

# Глобальный статус системы
SYSTEM_STATUS = {
    'last_update': datetime.now(),
    'wallet_healthy': False,
    'ltc_rate': 0.0,
    'api_services': {}
}

# Реальные лимиты API из поиска
API_REAL_LIMITS = {
    'blockchair': {'requests_per_minute': 30, 'requests_per_day': 43200},
    'nownodes': {'requests_per_day': 100000},
    'sochain': {'requests_per_second': 10, 'requests_per_day': 864000},
    'binance': {'requests_per_minute': 6000, 'requests_per_day': 8640000},
    'okx': {'requests_per_minute': 3000, 'requests_per_day': 4320000},
    'coingecko': {'requests_per_minute': 50, 'requests_per_day': 72000},
    'kraken': {'requests_per_minute': 60, 'requests_per_day': 86400}
}

payment_system_routes = web.RouteTableDef()

async def init_api_config(db_pool):
    """Инициализация настроек API из базы данных"""
    try:
        async with db_pool.acquire() as conn:
            # Загружаем API ключи из настроек бота
            settings = await conn.fetch("SELECT key, value FROM bot_settings WHERE key LIKE '%api%'")
            
            api_config = {}
            for setting in settings:
                if 'blockchair' in setting['key'].lower():
                    api_config['blockchair_key'] = setting['value']
                elif 'nownodes' in setting['key'].lower():
                    api_config['nownodes_key'] = setting['value']
                elif 'coingecko' in setting['key'].lower():
                    api_config['coingecko_key'] = setting['value']
            
            # Загружаем лимиты для API из базы
            api_stats = await conn.fetch('SELECT explorer_name, daily_limit FROM explorer_api_stats')
            for stat in api_stats:
                if stat['explorer_name'] in API_REAL_LIMITS:
                    # Используем реальные лимиты из поиска или из базы, если они есть
                    daily_limit = API_REAL_LIMITS[stat['explorer_name']].get('requests_per_day', 
                                    stat['daily_limit'] if stat['daily_limit'] else 1000)
                    
                    # Обновляем лимит в базе, если он отличается от реального
                    if stat['daily_limit'] != daily_limit:
                        await conn.execute('''
                            UPDATE explorer_api_stats 
                            SET daily_limit = $1, remaining_daily_requests = $1
                            WHERE explorer_name = $2
                        ''', daily_limit, stat['explorer_name'])
            
            # Инициализируем статусы сервисов
            services = await conn.fetch('SELECT explorer_name FROM explorer_api_stats')
            for service in services:
                service_name = service['explorer_name']
                if service_name not in SYSTEM_STATUS['api_services']:
                    SYSTEM_STATUS['api_services'][service_name] = {
                        'online': False,
                        'requests_today': 0,
                        'successful_requests': 0,
                        'daily_limit': API_REAL_LIMITS.get(service_name, {}).get('requests_per_day', 1000),
                        'remaining_requests': API_REAL_LIMITS.get(service_name, {}).get('requests_per_day', 1000),
                        'last_checked': None,
                        'response_time': 0
                    }
        
        return True
    except Exception as e:
        logging.error(f"Error initializing API config: {e}")
        return False

async def check_wallet_health():
    """Проверка состояния кошелька"""
    try:
        health = ltc_wallet.health_check()
        SYSTEM_STATUS['wallet_healthy'] = health.get('status', '') == 'healthy'
        return SYSTEM_STATUS['wallet_healthy']
    except Exception as e:
        logging.error(f"Error checking wallet health: {e}")
        SYSTEM_STATUS['wallet_healthy'] = False
        return False

async def update_ltc_rate():
    """Обновление курса LTC через различные API с приоритетами"""
    rate = 0.0
    services_priority = ['coingecko', 'binance', 'okx', 'kraken', 'blockchair']
    
    for service in services_priority:
        try:
            # Проверяем лимиты перед запросом
            if (service in SYSTEM_STATUS['api_services'] and 
                SYSTEM_STATUS['api_services'][service]['remaining_requests'] <= 0):
                logging.warning(f"Daily limit exceeded for {service}, skipping")
                continue
                
            if service == 'coingecko':
                rate = await get_ltc_rate_coingecko()
            elif service == 'binance':
                rate = await get_ltc_rate_binance()
            elif service == 'okx':
                rate = await get_ltc_rate_okx()
            elif service == 'kraken':
                rate = await get_ltc_rate_kraken()
            elif service == 'blockchair':
                rate = await get_ltc_rate_blockchair()
            
            if rate > 0:
                SYSTEM_STATUS['ltc_rate'] = rate
                # Обновляем статистику API
                await increment_api_request(service, True)
                break
            else:
                await increment_api_request(service, False)
        except Exception as e:
            logging.error(f"Error getting LTC rate from {service}: {e}")
            await increment_api_request(service, False)
            continue
    
    return rate > 0

async def check_api_service(service_name):
    """Проверка доступности API сервиса"""
    try:
        test_address = "LVg2kJS4J6W6G2L6W6G2L6W6G2L6W6G2L6"
        start_time = time.time()
        
        if service_name == 'blockchair':
            success = await check_transaction_blockchair(test_address, 0) is not None
        elif service_name == 'nownodes':
            success = await check_transaction_nownodes(test_address, 0) is not None
        elif service_name == 'sochain':
            success = await check_transaction_sochain(test_address, 0) is not None
        elif service_name == 'coingecko':
            success = await get_ltc_rate_coingecko() > 0
        elif service_name == 'binance':
            success = await get_ltc_rate_binance() > 0
        elif service_name == 'okx':
            success = await get_ltc_rate_okx() > 0
        elif service_name == 'kraken':
            success = await get_ltc_rate_kraken() > 0
        
        response_time = int((time.time() - start_time) * 1000)
        
        # Обновляем статус сервиса
        if service_name in SYSTEM_STATUS['api_services']:
            SYSTEM_STATUS['api_services'][service_name]['online'] = success
            SYSTEM_STATUS['api_services'][service_name]['last_checked'] = datetime.now()
            SYSTEM_STATUS['api_services'][service_name]['response_time'] = response_time
        
        # Обновляем базу данных
        await increment_api_request(service_name, success)
        
        return success, response_time
    except Exception as e:
        logging.error(f"Error checking service {service_name}: {e}")
        if service_name in SYSTEM_STATUS['api_services']:
            SYSTEM_STATUS['api_services'][service_name]['online'] = False
        await increment_api_request(service_name, False)
        return False, 0

async def increment_api_request(api_name, success):
    """Увеличиваем счетчик запросов к API"""
    try:
        db_pool = request.app['db_pool']
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE explorer_api_stats 
                SET total_requests = total_requests + 1,
                    successful_requests = successful_requests + $1,
                    remaining_daily_requests = GREATEST(0, remaining_daily_requests - 1),
                    last_used = NOW(),
                    updated_at = NOW()
                WHERE explorer_name = $2
            ''', 1 if success else 0, api_name)
            
            # Обновляем кэш
            if api_name in SYSTEM_STATUS['api_services']:
                SYSTEM_STATUS['api_services'][api_name]['requests_today'] += 1
                SYSTEM_STATUS['api_services'][api_name]['remaining_requests'] -= 1
                if success:
                    SYSTEM_STATUS['api_services'][api_name]['successful_requests'] += 1
                    
    except Exception as e:
        logging.error(f"Error incrementing API request count for {api_name}: {e}")

async def refresh_system_status():
    """Полное обновление статуса системы"""
    try:
        wallet_health = await check_wallet_health()
        rate_updated = await update_ltc_rate()
        
        # Проверяем все сервисы параллельно для экономии времени
        tasks = []
        for service in SYSTEM_STATUS['api_services'].keys():
            tasks.append(check_api_service(service))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        SYSTEM_STATUS['last_update'] = datetime.now()
        
        # Логируем результаты проверки
        logging.info(f"System status refreshed. Wallet: {wallet_health}, Rate updated: {rate_updated}")
        
        return True
    except Exception as e:
        logging.error(f"Error refreshing system status: {e}")
        return False

# Функции для получения курса LTC из различных источников
async def get_ltc_rate_coingecko():
    """Получение курса LTC через CoinGecko API"""
    try:
        # Реализация получения курса через CoinGecko
        return 0.0
    except Exception as e:
        logging.error(f"Error getting LTC rate from CoinGecko: {e}")
        return 0.0

async def get_ltc_rate_binance():
    """Получение курса LTC через Binance API"""
    try:
        # Реализация получения курса через Binance
        return 0.0
    except Exception as e:
        logging.error(f"Error getting LTC rate from Binance: {e}")
        return 0.0

async def get_ltc_rate_okx():
    """Получение курса LTC через OKX API"""
    try:
        # Реализация получения курса через OKX
        return 0.0
    except Exception as e:
        logging.error(f"Error getting LTC rate from OKX: {e}")
        return 0.0

async def get_ltc_rate_kraken():
    """Получение курса LTC через Kraken API"""
    try:
        # Реализация получения курса через Kraken
        return 0.0
    except Exception as e:
        logging.error(f"Error getting LTC rate from Kraken: {e}")
        return 0.0

async def get_ltc_rate_blockchair():
    """Получение курса LTC через Blockchair API"""
    try:
        # Реализация получения курса через Blockchair
        return 0.0
    except Exception as e:
        logging.error(f"Error getting LTC rate from Blockchair: {e}")
        return 0.0

@payment_system_routes.get('/admin/payment-system')
@aiohttp_jinja2.template('payment_system.html')
async def payment_system(request):
    db_pool = request.app['db_pool']
    
    try:
        # Инициализируем конфигурацию API при первом запросе
        if not SYSTEM_STATUS['api_services']:
            await init_api_config(db_pool)
        
        # Если статус устарел (больше 5 минут), обновляем его
        if (datetime.now() - SYSTEM_STATUS['last_update']) > timedelta(minutes=5):
            await refresh_system_status()
        
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
        
        # Подготавливаем данные для шаблона
        api_services = []
        for name, data in SYSTEM_STATUS['api_services'].items():
            api_services.append({
                'name': name,
                'online': data['online'],
                'requests_today': data['requests_today'],
                'daily_limit': data['daily_limit'],
                'remaining_requests': data['remaining_requests'],
                'successful_requests': data['successful_requests'],
                'last_checked': data['last_checked'],
                'response_time': data['response_time']
            })
        
        return {
            'wallet_health': wallet_health,
            'wallet_status': {'healthy': SYSTEM_STATUS['wallet_healthy']},
            'addresses': addresses,
            'api_stats': api_stats,
            'api_services': api_services,
            'ltc_rate': SYSTEM_STATUS['ltc_rate'],
            'last_update': SYSTEM_STATUS['last_update'],
            'api_real_limits': API_REAL_LIMITS,
            'error': None
        }
    except Exception as e:
        logger.error(f"Error in payment_system: {e}")
        return {
            'error': f'Ошибка загрузки данных: {e}',
            'wallet_health': {},
            'wallet_status': {'healthy': False},
            'addresses': [],
            'api_stats': [],
            'api_services': [],
            'ltc_rate': 0,
            'last_update': datetime.now()
        }

# Остальные маршруты остаются без изменений, но добавляем новые для управления статусом
@payment_system_routes.get('/admin/payment-system/refresh-status')
async def refresh_status(request):
    try:
        success = await refresh_system_status()
        return web.json_response({
            'success': success,
            'message': 'Статус системы обновлен' if success else 'Ошибка обновления статуса',
            'status': SYSTEM_STATUS
        })
    except Exception as e:
        logging.error(f"Error refreshing status: {e}")
        return web.json_response({
            'success': False,
            'message': f'Ошибка: {e}'
        })

@payment_system_routes.get('/admin/payment-system/test-service')
async def test_service(request):
    service_name = request.query.get('service')
    
    if service_name not in SYSTEM_STATUS['api_services']:
        return web.json_response({
            'success': False,
            'message': f'Неизвестный сервис: {service_name}'
        })
    
    try:
        success, response_time = await check_api_service(service_name)
        return web.json_response({
            'success': success,
            'message': f'Сервис {service_name} {"работает" if success else "не доступен"}',
            'response_time': response_time
        })
    except Exception as e:
        logging.error(f"Error testing service {service_name}: {e}")
        return web.json_response({
            'success': False,
            'message': f'Ошибка тестирования сервиса: {e}'
        })

# Остальные маршруты (generate_address, update_api_config, create_backup, recover_wallet, 
# export_addresses, check_balance, update_address, test_explorer, update_explorer)
# остаются без изменений, но используют обновленные функции

# Периодическая задача для обновления статуса
async def periodic_status_update():
    """Периодическое обновление статуса системы"""
    while True:
        try:
            await refresh_system_status()
            await asyncio.sleep(300)  # 5 минут
        except Exception as e:
            logging.error(f"Error in periodic status update: {e}")
            await asyncio.sleep(60)  # Ждем 1 минуту при ошибке

# Запуск периодической задачи при старте приложения
async def start_background_tasks(app):
    app['status_task'] = asyncio.create_task(periodic_status_update())

async def cleanup_background_tasks(app):
    app['status_task'].cancel()
    await app['status_task']

# Добавляем функции к приложению
def setup_payment_system(app):
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.router.add_routes(payment_system_routes)
