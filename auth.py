import os
import jwt
from aiohttp import web
import aiohttp_jinja2
from datetime import datetime, timedelta, timezone

# Настройки
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'password')
JWT_SECRET = os.getenv('JWT_SECRET', 'your-secret-key')

# Middleware для проверки аутентификации
@web.middleware
async def auth_middleware(request, handler):
    if request.url.path.startswith('/admin/login') or request.url.path == '/admin':
        return await handler(request)
    
    token = request.cookies.get('auth_token')
    if not token:
        return web.HTTPFound('/admin/login')
    
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        request['user'] = payload
    except jwt.InvalidTokenError:
        response = web.HTTPFound('/admin/login')
        response.del_cookie('auth_token')
        return response
    
    return await handler(request)

# Маршруты аутентификации
auth_routes = web.RouteTableDef()

@auth_routes.get('/admin')
async def admin_redirect(request):
    return web.HTTPFound('/admin/login')

@auth_routes.get('/admin/login')
@aiohttp_jinja2.template('login.html')
async def login_form(request):
    error = request.query.get('error')
    return {'error': error}

@auth_routes.post('/admin/login')
async def login(request):
    data = await request.post()
    username = data.get('username')
    password = data.get('password')
    
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = jwt.encode({
            'username': username,
            'exp': datetime.now(timezone.utc) + timedelta(hours=24)
        }, JWT_SECRET, algorithm='HS256')
        
        response = web.HTTPFound('/admin/dashboard')
        response.set_cookie('auth_token', token, httponly=True, max_age=86400)
        return response
    else:
        return web.HTTPFound('/admin/login?error=1')

@auth_routes.get('/admin/logout')
async def logout(request):
    response = web.HTTPFound('/admin/login')
    response.del_cookie('auth_token')
    return response
