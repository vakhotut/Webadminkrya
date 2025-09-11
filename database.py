import os
import logging
import ssl
import asyncpg

logger = logging.getLogger(__name__)

async def init_db(app):
    try:
        # Для Render нам нужно использовать SSL соединение
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        # Подключаемся к базе данных
        app['db_pool'] = await asyncpg.create_pool(
            os.environ.get('DATABASE_URL'),
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
