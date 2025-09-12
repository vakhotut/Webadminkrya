from aiohttp import web
import aiohttp_jinja2

bot_management_routes = web.RouteTableDef()

@bot_management_routes.get('/admin/bot-management')
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
                    'cities': [],
                    'districts': [],
                    'products': [],
                    'delivery_types': [],
                    'bot_settings': {}
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
            
            # Загружаем настройки бота
            bot_settings_rows = await conn.fetch('SELECT * FROM bot_settings')
            bot_settings = {row['key']: row['value'] for row in bot_settings_rows}
        
        return {
            'texts': texts,
            'languages': [lang['lang'] for lang in languages],
            'cities': cities,
            'districts': districts,
            'products': products,
            'delivery_types': delivery_types,
            'bot_settings': bot_settings
        }
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in bot_management: {e}")
        return {
            'error': f'Ошибка загрузки данных бота: {e}',
            'texts': [],
            'languages': [],
            'cities': [],
            'districts': [],
            'products': [],
            'delivery_types': [],
            'bot_settings': {}
        }

@bot_management_routes.post('/admin/bot/texts/update')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in update_text: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#texts')

@bot_management_routes.post('/admin/bot/texts/add')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in add_text: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#texts')

@bot_management_routes.post('/admin/bot/texts/delete/{text_id}')
async def delete_text(request):
    text_id = int(request.match_info['text_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM texts WHERE id = $1', text_id)
        
        return web.HTTPFound('/admin/bot-management#texts')
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in delete_text: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#texts')

@bot_management_routes.post('/admin/bot/cities/update')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in update_city: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#cities')

@bot_management_routes.post('/admin/bot/cities/add')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in add_city: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#cities')

@bot_management_routes.post('/admin/bot/cities/delete/{city_id}')
async def delete_city(request):
    city_id = int(request.match_info['city_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM cities WHERE id = $1', city_id)
        
        return web.HTTPFound('/admin/bot-management#cities')
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in delete_city: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#cities')

@bot_management_routes.post('/admin/bot/districts/update')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in update_district: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#districts')

@bot_management_routes.post('/admin/bot/districts/add')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in add_district: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#districts')

@bot_management_routes.post('/admin/bot/districts/delete/{district_id}')
async def delete_district(request):
    district_id = int(request.match_info['district_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM districts WHERE id = $1', district_id)
        
        return web.HTTPFound('/admin/bot-management#districts')
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in delete_district: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#districts')

@bot_management_routes.post('/admin/bot/products/update')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in update_product_bot: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#products')

@bot_management_routes.post('/admin/bot/products/add')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in add_product_bot: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#products')

@bot_management_routes.post('/admin/bot/products/delete/{product_id}')
async def delete_product_bot(request):
    product_id = int(request.match_info['product_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM products WHERE id = $1', product_id)
        
        return web.HTTPFound('/admin/bot-management#products')
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in delete_product_bot: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#products')

@bot_management_routes.post('/admin/bot/delivery-types/update')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in update_delivery_type: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#delivery')

@bot_management_routes.post('/admin/bot/delivery-types/add')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in add_delivery_type: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#delivery')

@bot_management_routes.post('/admin/bot/delivery-types/delete/{type_id}')
async def delete_delivery_type(request):
    type_id = int(request.match_info['type_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM delivery_types WHERE id = $1', type_id)
        
        return web.HTTPFound('/admin/bot-management#delivery')
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in delete_delivery_type: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#delivery')

@bot_management_routes.post('/admin/bot/settings/update')
async def update_bot_settings(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            for key in data:
                if key in ['operator_link', 'support_link', 'rules_link', 'channel_link', 
                          'reviews_link', 'website_link', 'main_menu_image', 'balance_menu_image',
                          'category_menu_image', 'district_menu_image', 'delivery_menu_image',
                          'confirmation_menu_image']:
                    await conn.execute('''
                        INSERT INTO bot_settings (key, value)
                        VALUES ($1, $2)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    ''', key, data[key])
        
        return web.HTTPFound('/admin/bot-management#links')
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in update_bot_settings: {e}")
        return web.HTTPFound('/admin/bot-management?error=1#links')
