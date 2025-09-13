import logging
from aiohttp import web
import aiohttp_jinja2
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

users_routes = web.RouteTableDef()

@users_routes.get('/admin/dashboard')
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
                'SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE'
            )
            
            # Статистика заказов
            total_orders = await conn.fetchval('SELECT COUNT(*) FROM purchases')
            today_orders = await conn.fetchval(
                'SELECT COUNT(*) FROM purchases WHERE purchase_time >= CURRENT_DATE'
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
                'SELECT COALESCE(SUM(price), 0) FROM purchases WHERE purchase_time >= CURRENT_DATE'
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
        import logging
        logger = logging.getLogger(__name__)
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

@users_routes.get('/admin/users')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in users_list: {e}")
        return {
            'error': f'Ошибка загрузки пользователей: {e}',
            'users': [],
            'page': 1,
            'total_pages': 0
        }

@users_routes.post('/admin/users/{user_id}/ban')
async def ban_user(request):
    user_id = int(request.match_info['user_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            # Бан на 24 часа
            ban_until = datetime.now() + timedelta(hours=24)
            await conn.execute(
                'UPDATE users SET ban_until = $1 WHERE user_id = $2',
                ban_until, user_id
            )
        
        return web.HTTPFound('/admin/users?message=Пользователь заблокирован')
    except Exception as e:
        logger.error(f"Error banning user {user_id}: {e}")
        return web.HTTPFound('/admin/users?error=Ошибка при блокировке пользователя')

@users_routes.post('/admin/users/{user_id}/unban')
async def unban_user(request):
    user_id = int(request.match_info['user_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'UPDATE users SET ban_until = NULL WHERE user_id = $1',
                user_id
            )
        
        return web.HTTPFound('/admin/users?message=Пользователь разблокирован')
    except Exception as e:
        logger.error(f"Error unbanning user {user_id}: {e}")
        return web.HTTPFound('/admin/users?error=Ошибка при разблокировке пользователя')

@users_routes.post('/admin/users/{user_id}/balance')
async def change_balance(request):
    user_id = int(request.match_info['user_id'])
    db_pool = request.app['db_pool']
    data = await request.post()
    
    try:
        amount = float(data['amount'])
        is_subtract = 'is_subtract' in data
        
        async with db_pool.acquire() as conn:
            if is_subtract:
                await conn.execute(
                    'UPDATE users SET balance = balance - $1 WHERE user_id = $2',
                    amount, user_id
                )
            else:
                await conn.execute(
                    'UPDATE users SET balance = balance + $1 WHERE user_id = $2',
                    amount, user_id
                )
        
        action = "вычтена из" if is_subtract else "добавлена к"
        return web.HTTPFound(f'/admin/users?message=${amount} {action} балансу пользователя')
    except Exception as e:
        logger.error(f"Error changing balance for user {user_id}: {e}")
        return web.HTTPFound('/admin/users?error=Ошибка при изменении баланса')

@users_routes.post('/admin/users/{user_id}/discount')
async def change_discount(request):
    user_id = int(request.match_info['user_id'])
    db_pool = request.app['db_pool']
    data = await request.post()
    
    try:
        discount = int(data['discount'])
        is_temporary = 'is_temporary' in data
        
        async with db_pool.acquire() as conn:
            if is_temporary:
                # Для временной скидки можно добавить отдельное поле в БД
                # Пока просто устанавливаем скидку
                await conn.execute(
                    'UPDATE users SET discount = $1 WHERE user_id = $2',
                    discount, user_id
                )
            else:
                # Постоянная скидка
                await conn.execute(
                    'UPDATE users SET discount = $1 WHERE user_id = $2',
                    discount, user_id
                )
        
        discount_type = "временная" if is_temporary else "постоянная"
        return web.HTTPFound(f'/admin/users?message={discount_type.capitalize()} скидка установлена на {discount}%')
    except Exception as e:
        logger.error(f"Error changing discount for user {user_id}: {e}")
        return web.HTTPFound('/admin/users?error=Ошибка при изменении скидки')

@users_routes.post('/admin/users/{user_id}/delete')
async def delete_user(request):
    user_id = int(request.match_info['user_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            # Удаляем связанные данные пользователя
            await conn.execute('DELETE FROM transactions WHERE user_id = $1', user_id)
            await conn.execute('DELETE FROM purchases WHERE user_id = $1', user_id)
            
            # Удаляем самого пользователя
            await conn.execute('DELETE FROM users WHERE user_id = $1', user_id)
        
        return web.HTTPFound('/admin/users?message=Пользователь полностью удален')
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}")
        return web.HTTPFound('/admin/users?error=Ошибка при удалении пользователя')
