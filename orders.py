import logging
from aiohttp import web
import aiohttp_jinja2

logger = logging.getLogger(__name__)

orders_routes = web.RouteTableDef()

@orders_routes.get('/admin/orders')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in orders_list: {e}")
        return {
            'error': f'Ошибка загрузки заказов: {e}',
            'orders': [],
            'page': 1,
            'total_pages': 0
        }
