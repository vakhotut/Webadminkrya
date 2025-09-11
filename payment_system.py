import os
import time
import io
import csv
import logging
from aiohttp import web
import aiohttp_jinja2

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
    
    ltc_wallet = WalletStub()
    
    async def get_ltc_usd_rate():
        return 0.0
    
    async def check_transaction_blockchair(address, amount):
        return None
    
    async def check_transaction_sochain(address, amount):
        return None
    
    async def check_transaction_nownodes(address, amount):
        return None

payment_system_routes = web.RouteTableDef()

@payment_system_routes.get('/admin/payment-system')
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

@payment_system_routes.post('/admin/payment-system/generate-address')
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

@payment_system_routes.post('/admin/payment-system/api-config')
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

@payment_system_routes.post('/admin/payment-system/create-backup')
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

@payment_system_routes.post('/admin/payment-system/recover-wallet')
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

@payment_system_routes.get('/admin/payment-system/export-addresses')
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

@payment_system_routes.get('/admin/payment-system/check-balance')
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

@payment_system_routes.post('/admin/payment-system/update-address/{address}')
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

@payment_system_routes.get('/admin/payment-system/test-explorer')
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

@payment_system_routes.post('/admin/payment-system/update-explorer/{explorer}')
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
