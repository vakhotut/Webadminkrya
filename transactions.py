from aiohttp import web
import aiohttp_jinja2

transactions_routes = web.RouteTableDef()

@transactions_routes.get('/admin/transactions')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in transactions_list: {e}")
        return {
            'error': f'Ошибка загрузки транзакций: {e}',
            'transactions': [],
            'page': 1,
            'total_pages': 0
        }

@transactions_routes.post('/admin/transactions/{transaction_id}/cancel')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in cancel_transaction: {e}")
        return web.HTTPFound('/admin/transactions?error=1')
