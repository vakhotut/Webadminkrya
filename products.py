import uuid
from aiohttp import web
import aiohttp_jinja2

products_routes = web.RouteTableDef()

@products_routes.get('/admin/products')
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
                ORDER BY p.id DESC
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
        import logging
        logger = logging.getLogger(__name__)
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in add_product: {e}")
        return web.HTTPFound('/admin/products?error=1')

@products_routes.post('/admin/products/update/{product_id}')
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
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in update_product: {e}")
        return web.HTTPFound('/admin/products?error=1')

@products_routes.post('/admin/products/delete/{product_id}')
async def delete_product(request):
    product_id = int(request.match_info['product_id'])
    db_pool = request.app['db_pool']
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM products WHERE id = $1', product_id)
        
        return web.HTTPFound('/admin/products')
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in delete_product: {e}")
        return web.HTTPFound('/admin/products?error=1')
