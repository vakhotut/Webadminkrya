import uuid
from aiohttp import web
import aiohttp_jinja2
import logging

logger = logging.getLogger(__name__)

products_routes = web.RouteTableDef()

@products_routes.get('/admin/products')
@aiohttp_jinja2.template('products.html')
async def products_list(request):
    db_pool = request.app['db_pool']
    page = int(request.query.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    # Определяем активную вкладку
    active_tab = request.query.get('tab', 'catalog')
    
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
                    'subcategories': [],
                    'districts': [],
                    'delivery_types': [],
                    'sold_products': [],
                    'page': 1,
                    'total_pages': 0,
                    'active_tab': active_tab
                }
            
            # Получаем данные в зависимости от активной вкладки
            if active_tab == 'catalog':
                products = await conn.fetch('''
                    SELECT p.*, c.name as city_name, cat.name as category_name,
                           s.name as subcategory_name, s.quantity as subcategory_quantity,
                           d.name as district_name, dt.name as delivery_type_name
                    FROM products p
                    LEFT JOIN cities c ON p.city_id = c.id
                    LEFT JOIN categories cat ON p.category_id = cat.id
                    LEFT JOIN subcategories s ON p.subcategory_id = s.id
                    LEFT JOIN districts d ON p.district_id = d.id
                    LEFT JOIN delivery_types dt ON p.delivery_type_id = dt.id
                    ORDER BY p.id DESC
                    LIMIT $1 OFFSET $2
                ''', per_page, offset)
                
                total_products = await conn.fetchval('SELECT COUNT(*) FROM products')
                total_pages = (total_products + per_page - 1) // per_page
                
            elif active_tab == 'sold':
                products = await conn.fetch('''
                    SELECT sp.*, p.name as product_name, s.name as subcategory_name,
                           u.user_id, u.username, u.first_name, sp.sold_at, 
                           sp.sold_price, sp.quantity, s.quantity as remaining_quantity
                    FROM sold_products sp
                    LEFT JOIN products p ON sp.product_id = p.id
                    LEFT JOIN subcategories s ON sp.subcategory_id = s.id
                    LEFT JOIN users u ON sp.user_id = u.user_id
                    ORDER BY sp.sold_at DESC
                    LIMIT $1 OFFSET $2
                ''', per_page, offset)
                
                total_products = await conn.fetchval('SELECT COUNT(*) FROM sold_products')
                total_pages = (total_products + per_page - 1) // per_page
            else:
                products = []
                total_pages = 0
            
            # Всегда загружаем данные для форм
            cities_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'cities')"
            )
            categories_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'categories')"
            )
            subcategories_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'subcategories')"
            )
            districts_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'districts')"
            )
            delivery_types_table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'delivery_types')"
            )
            
            cities = []
            categories = []
            subcategories = []
            districts = []
            delivery_types = []
            
            if cities_table_exists:
                cities = await conn.fetch('SELECT * FROM cities ORDER BY name')
            
            if categories_table_exists:
                categories = await conn.fetch('SELECT * FROM categories ORDER BY name')
            
            if subcategories_table_exists:
                subcategories = await conn.fetch('SELECT * FROM subcategories ORDER BY name')
            
            if districts_table_exists:
                districts = await conn.fetch('SELECT * FROM districts ORDER BY name')
            
            if delivery_types_table_exists:
                delivery_types = await conn.fetch('SELECT * FROM delivery_types ORDER BY name')
            
            # Загружаем отдельно проданные товары для соответствующей вкладки
            sold_products = []
            
            if active_tab == 'sold':
                sold_products = await conn.fetch('''
                    SELECT sp.*, p.name as product_name, s.name as subcategory_name,
                           u.user_id, u.username, u.first_name, sp.sold_at, 
                           sp.sold_price, sp.quantity, s.quantity as remaining_quantity
                    FROM sold_products sp
                    LEFT JOIN products p ON sp.product_id = p.id
                    LEFT JOIN subcategories s ON sp.subcategory_id = s.id
                    LEFT JOIN users u ON sp.user_id = u.user_id
                    ORDER BY sp.sold_at DESC
                    LIMIT 50
                ''')
        
        return {
            'products': products,
            'cities': cities,
            'categories': categories,
            'subcategories': subcategories,
            'districts': districts,
            'delivery_types': delivery_types,
            'sold_products': sold_products,
            'page': page,
            'total_pages': total_pages,
            'active_tab': active_tab
        }
    except Exception as e:
        logger.error(f"Error in products_list: {e}")
        return {
            'error': f'Ошибка загрузки товаров: {e}',
            'products': [],
            'cities': [],
            'categories': [],
            'subcategories': [],
            'districts': [],
            'delivery_types': [],
            'sold_products': [],
            'page': 1,
            'total_pages': 0,
            'active_tab': active_tab
        }

@products_routes.post('/admin/products/add')
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
            
            # Если выбрана новая подкатегория, создаем ее
            if data['subcategory_id'] == 'new':
                subcategory_id = await conn.fetchval(
                    'INSERT INTO subcategories (category_id, name, quantity) VALUES ($1, $2, $3) RETURNING id',
                    category_id, data['new_subcategory'], int(data['quantity'])
                )
            else:
                subcategory_id = int(data['subcategory_id'])
                # Обновляем количество в существующей подкатегории
                await conn.execute(
                    'UPDATE subcategories SET quantity = quantity + $1 WHERE id = $2',
                    int(data['quantity']), subcategory_id
                )
            
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
                (uuid, name, description, price, image_url, category_id, subcategory_id, city_id, district_id, delivery_type_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ''', product_uuid, data['name'], data['description'], float(data['price']), 
               data['image_url'], category_id, subcategory_id, city_id, district_id, delivery_type_id)
        
        return web.HTTPFound('/admin/products?tab=catalog')
    except Exception as e:
        logger.error(f"Error in add_product: {e}")
        return web.HTTPFound('/admin/products?tab=add&error=1')

@products_routes.post('/admin/products/update/{product_id}')
async def update_product(request):
    product_id = int(request.match_info['product_id'])
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            # Получаем текущий товар
            product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
            
            # Если изменилась подкатегория, обновляем количество
            if 'subcategory_id' in data and data['subcategory_id'] != str(product['subcategory_id']):
                # Уменьшаем количество в старой подкатегории
                await conn.execute('''
                    UPDATE subcategories 
                    SET quantity = quantity - 1 
                    WHERE id = $1
                ''', product['subcategory_id'])
                
                # Увеличиваем количество в новой подкатегории
                await conn.execute('''
                    UPDATE subcategories 
                    SET quantity = quantity + 1 
                    WHERE id = $1
                ''', int(data['subcategory_id']))
            
            await conn.execute('''
                UPDATE products 
                SET name = $1, description = $2, price = $3, image_url = $4,
                    category_id = $5, subcategory_id = $6, city_id = $7, 
                    district_id = $8, delivery_type_id = $9,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = $10
            ''', data['name'], data['description'], float(data['price']), data['image_url'],
               int(data['category_id']), int(data['subcategory_id']), int(data['city_id']), 
               int(data['district_id']), int(data['delivery_type_id']), product_id)
        
        return web.HTTPFound('/admin/products?tab=catalog')
    except Exception as e:
        logger.error(f"Error in update_product: {e}")
        return web.HTTPFound('/admin/products?tab=catalog&error=1')

@products_routes.post('/admin/products/delete/{product_id}')
async def delete_product(request):
    product_id = int(request.match_info['product_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            # Получаем информацию о товаре
            product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
            
            if product:
                # Уменьшаем количество в подкатегории
                await conn.execute('''
                    UPDATE subcategories 
                    SET quantity = quantity - 1 
                    WHERE id = $1
                ''', product['subcategory_id'])
                
                # Удаляем товар
                await conn.execute('DELETE FROM products WHERE id = $1', product_id)
        
        return web.HTTPFound('/admin/products?tab=catalog')
    except Exception as e:
        logger.error(f"Error in delete_product: {e}")
        return web.HTTPFound('/admin/products?tab=catalog&error=1')

@products_routes.post('/admin/subcategories/add')
async def add_subcategory(request):
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO subcategories (category_id, name, quantity)
                VALUES ($1, $2, $3)
            ''', int(data['category_id']), data['name'], int(data['quantity']))
        
        return web.HTTPFound('/admin/products?tab=catalog')
    except Exception as e:
        logger.error(f"Error in add_subcategory: {e}")
        return web.HTTPFound('/admin/products?tab=catalog&error=1')

@products_routes.post('/admin/subcategories/update/{subcategory_id}')
async def update_subcategory(request):
    subcategory_id = int(request.match_info['subcategory_id'])
    data = await request.post()
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE subcategories 
                SET name = $1, quantity = $2
                WHERE id = $3
            ''', data['name'], int(data['quantity']), subcategory_id)
        
        return web.HTTPFound('/admin/products?tab=catalog')
    except Exception as e:
        logger.error(f"Error in update_subcategory: {e}")
        return web.HTTPFound('/admin/products?tab=catalog&error=1')

@products_routes.post('/admin/subcategories/delete/{subcategory_id}')
async def delete_subcategory(request):
    subcategory_id = int(request.match_info['subcategory_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            # Удаляем все товары этой подкатегории
            await conn.execute('DELETE FROM products WHERE subcategory_id = $1', subcategory_id)
            
            # Удаляем подкатегорию
            await conn.execute('DELETE FROM subcategories WHERE id = $1', subcategory_id)
        
        return web.HTTPFound('/admin/products?tab=catalog')
    except Exception as e:
        logger.error(f"Error in delete_subcategory: {e}")
        return web.HTTPFound('/admin/products?tab=catalog&error=1')
