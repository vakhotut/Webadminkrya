import os
import logging
import aiohttp
import aiohttp_jinja2
from aiohttp import web
import json
import asyncio
import subprocess
from datetime import datetime
from database import db_pool

logger = logging.getLogger(__name__)

# Маршруты для настроек
settings_routes = web.RouteTableDef()

@settings_routes.get('/admin/settings')
@aiohttp_jinja2.template('settings.html')
async def settings_page(request):
    # Получаем текущие настройки из БД или переменных окружения
    settings = {
        'bot_token': os.getenv('BOT_TOKEN', ''),
        'admin_login': os.getenv('ADMIN_LOGIN', ''),
        'admin_password': os.getenv('ADMIN_PASSWORD', ''),
        'group_id': os.getenv('GROUP_ID', ''),
        'chat_id': os.getenv('CHAT_ID', ''),
        'blockchair_api_key': os.getenv('BLOCKCHAIR_API_KEY', ''),
        'nownodes_api_key': os.getenv('NOWNODES_API_KEY', ''),
        'database_url': os.getenv('DATABASE_URL', '')
    }
    return settings

@settings_routes.post('/admin/settings/save')
async def save_settings(request):
    data = await request.post()
    
    # Обновляем настройки в переменных окружения
    os.environ['BOT_TOKEN'] = data.get('bot_token', '')
    os.environ['ADMIN_LOGIN'] = data.get('admin_login', '')
    os.environ['ADMIN_PASSWORD'] = data.get('admin_password', '')
    os.environ['GROUP_ID'] = data.get('group_id', '')
    os.environ['CHAT_ID'] = data.get('chat_id', '')
    os.environ['BLOCKCHAIR_API_KEY'] = data.get('blockchair_api_key', '')
    os.environ['NOWNODES_API_KEY'] = data.get('nownodes_api_key', '')
    
    # Сохраняем в файл .env
    with open('.env', 'w') as f:
        for key, value in data.items():
            if key != 'backup_file':  # Пропускаем поле файла
                f.write(f"{key.upper()}={value}\n")
    
    return web.Response(text="Настройки сохранены!")

@settings_routes.get('/admin/backup')
async def create_backup(request):
    # Создание backup базы данных
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"backup_{timestamp}.sql"
        
        # Команда для создания backup PostgreSQL
        db_url = os.environ['DATABASE_URL']
        process = await asyncio.create_subprocess_exec(
            'pg_dump', db_url, '-f', backup_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"Backup error: {stderr.decode()}")
            return web.Response(text=f"Ошибка создания backup: {stderr.decode()}")
        
        return web.FileResponse(
            backup_file,
            headers={
                'Content-Disposition': f'attachment; filename="{backup_file}"'
            }
        )
    except Exception as e:
        logger.error(f"Backup error: {e}")
        return web.Response(text=f"Ошибка создания backup: {e}")

@settings_routes.post('/admin/restore')
async def restore_backup(request):
    data = await request.post()
    backup_file = data['backup_file']
    
    try:
        # Сохраняем загруженный файл
        file_content = backup_file.file.read()
        temp_file = f"temp_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
        
        with open(temp_file, 'wb') as f:
            f.write(file_content)
        
        # Команда для восстановления backup PostgreSQL
        db_url = os.environ['DATABASE_URL']
        process = await asyncio.create_subprocess_exec(
            'psql', db_url, '-f', temp_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        # Удаляем временный файл
        os.remove(temp_file)
        
        if process.returncode != 0:
            logger.error(f"Restore error: {stderr.decode()}")
            return web.Response(text=f"Ошибка восстановления: {stderr.decode()}")
        
        return web.Response(text="База данных восстановлена!")
    except Exception as e:
        logger.error(f"Restore error: {e}")
        return web.Response(text=f"Ошибка восстановления: {e}")

@settings_routes.get('/admin/settings/export')
async def export_settings(request):
    # Экспорт всех настроек в JSON файл
    settings = {
        'BOT_TOKEN': os.getenv('BOT_TOKEN', ''),
        'ADMIN_LOGIN': os.getenv('ADMIN_LOGIN', ''),
        'ADMIN_PASSWORD': os.getenv('ADMIN_PASSWORD', ''),
        'GROUP_ID': os.getenv('GROUP_ID', ''),
        'CHAT_ID': os.getenv('CHAT_ID', ''),
        'BLOCKCHAIR_API_KEY': os.getenv('BLOCKCHAIR_API_KEY', ''),
        'NOWNODES_API_KEY': os.getenv('NOWNODES_API_KEY', ''),
        'DATABASE_URL': os.getenv('DATABASE_URL', '')
    }
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"settings_export_{timestamp}.json"
    
    with open(filename, 'w') as f:
        json.dump(settings, f, indent=4)
    
    return web.FileResponse(
        filename,
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
    )

@settings_routes.post('/admin/settings/import')
async def import_settings(request):
    data = await request.post()
    settings_file = data['settings_file']
    
    try:
        # Читаем загруженный файл
        file_content = settings_file.file.read().decode()
        settings = json.loads(file_content)
        
        # Обновляем настройки
        for key, value in settings.items():
            os.environ[key] = value
        
        # Сохраняем в .env
        with open('.env', 'w') as f:
            for key, value in settings.items():
                f.write(f"{key}={value}\n")
        
        return web.Response(text="Настройки импортированы!")
    except Exception as e:
        logger.error(f"Import error: {e}")
        return web.Response(text=f"Ошибка импорта: {e}")
