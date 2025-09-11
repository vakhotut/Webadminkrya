from aiohttp import web
import aiohttp_jinja2

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
