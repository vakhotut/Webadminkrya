# admin.py
from flask import Flask, render_template, request, redirect, url_for, session, flash
import asyncpg
import os
from datetime import datetime, timedelta
import asyncio
import threading

app = Flask(__name__)
app.secret_key = os.environ.get('ADMIN_SECRET_KEY', 'your-secret-key-here')

# Конфигурация базы данных
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://user:pass@localhost/dbname')

# Функции для работы с БД (синхронные обертки)
async def async_get_users():
    conn = await asyncpg.connect(DATABASE_URL)
    users = await conn.fetch('SELECT * FROM users ORDER BY created_at DESC')
    await conn.close()
    return users

async def async_get_transactions():
    conn = await asyncpg.connect(DATABASE_URL)
    transactions = await conn.fetch('''
        SELECT t.*, u.username, u.first_name 
        FROM transactions t 
        LEFT JOIN users u ON t.user_id = u.user_id 
        ORDER BY created_at DESC
    ''')
    await conn.close()
    return transactions

async def async_get_purchases():
    conn = await asyncpg.connect(DATABASE_URL)
    purchases = await conn.fetch('''
        SELECT p.*, u.username, u.first_name 
        FROM purchases p 
        LEFT JOIN users u ON p.user_id = u.user_id 
        ORDER BY purchase_time DESC
    ''')
    await conn.close()
    return purchases

async def async_get_stats():
    conn = await asyncpg.connect(DATABASE_URL)
    
    # Общая статистика
    total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
    total_orders = await conn.fetchval('SELECT COUNT(*) FROM purchases')
    total_revenue = await conn.fetchval('SELECT COALESCE(SUM(price), 0) FROM purchases WHERE status = "completed"')
    
    # Статистика за последние 7 дней
    week_ago = datetime.now() - timedelta(days=7)
    weekly_users = await conn.fetchval('SELECT COUNT(*) FROM users WHERE created_at >= $1', week_ago)
    weekly_orders = await conn.fetchval('SELECT COUNT(*) FROM purchases WHERE purchase_time >= $1', week_ago)
    weekly_revenue = await conn.fetchval('SELECT COALESCE(SUM(price), 0) FROM purchases WHERE purchase_time >= $1 AND status = "completed"', week_ago)
    
    await conn.close()
    
    return {
        'total_users': total_users,
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'weekly_users': weekly_users,
        'weekly_orders': weekly_orders,
        'weekly_revenue': weekly_revenue
    }

# Синхронные обертки для использования во Flask
def get_users():
    return asyncio.run(async_get_users())

def get_transactions():
    return asyncio.run(async_get_transactions())

def get_purchases():
    return asyncio.run(async_get_purchases())

def get_stats():
    return asyncio.run(async_get_stats())

# Маршруты админ-панели
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Проверка учетных данных (замените на свою логику)
        if username == os.environ.get('ADMIN_USERNAME', 'admin') and password == os.environ.get('ADMIN_PASSWORD', 'admin'):
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Неверные учетные данные', 'error')
    
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    stats = get_stats()
    recent_users = get_users()[:5]
    recent_transactions = get_transactions()[:5]
    
    return render_template('admin/dashboard.html', 
                         stats=stats, 
                         recent_users=recent_users,
                         recent_transactions=recent_transactions)

@app.route('/admin/users')
def admin_users():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    users = get_users()
    return render_template('admin/users.html', users=users)

@app.route('/admin/transactions')
def admin_transactions():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    transactions = get_transactions()
    return render_template('admin/transactions.html', transactions=transactions)

@app.route('/admin/orders')
def admin_orders():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    purchases = get_purchases()
    return render_template('admin/orders.html', purchases=purchases)

if __name__ == '__main__':
    port = int(os.environ.get('ADMIN_PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=True)
