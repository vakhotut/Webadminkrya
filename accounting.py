import io
import csv
import json
import logging
from aiohttp import web
import aiohttp_jinja2
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

logger = logging.getLogger(__name__)

accounting_routes = web.RouteTableDef()

@accounting_routes.get('/admin/accounting')
@aiohttp_jinja2.template('accounting.html')
async def accounting(request):
    db_pool = request.app['db_pool']
    
    # Получаем параметры фильтрации
    start_date = request.query.get('start_date')
    end_date = request.query.get('end_date')
    report_type = request.query.get('report_type', 'sales')
    
    try:
        async with db_pool.acquire() as conn:
            # Базовые запросы для разных типов отчетов
            if report_type == 'sales':
                # Отчет по продажам
                query = '''
                    SELECT p.*, u.username, u.first_name 
                    FROM purchases p 
                    LEFT JOIN users u ON p.user_id = u.user_id 
                    WHERE 1=1
                '''
                count_query = 'SELECT COUNT(*) FROM purchases p WHERE 1=1'
                params = []
                param_count = 0
                
                if start_date:
                    param_count += 1
                    query += f" AND p.purchase_time >= ${param_count}"
                    count_query += f" AND p.purchase_time >= ${param_count}"
                    params.append(start_date)
                if end_date:
                    param_count += 1
                    query += f" AND p.purchase_time <= ${param_count}"
                    count_query += f" AND p.purchase_time <= ${param_count}"
                    params.append(end_date + ' 23:59:59')
                
                query += ' ORDER BY p.purchase_time DESC'
                
                records = await conn.fetch(query, *params)
                total_count = await conn.fetchval(count_query, *params)
                
                # Статистика
                revenue_query = 'SELECT COALESCE(SUM(price), 0) FROM purchases p WHERE 1=1'
                revenue_params = []
                param_count_revenue = 0
                if start_date:
                    param_count_revenue += 1
                    revenue_query += f" AND p.purchase_time >= ${param_count_revenue}"
                    revenue_params.append(start_date)
                if end_date:
                    param_count_revenue += 1
                    revenue_query += f" AND p.purchase_time <= ${param_count_revenue}"
                    revenue_params.append(end_date + ' 23:59:59')
                
                total_revenue = await conn.fetchval(revenue_query, *revenue_params)
                
                return {
                    'records': records,
                    'total_count': total_count,
                    'total_revenue': total_revenue,
                    'report_type': report_type,
                    'start_date': start_date,
                    'end_date': end_date
                }
                
            elif report_type == 'refunds':
                # Отчет по возвратам
                query = '''
                    SELECT t.*, u.username, u.first_name 
                    FROM transactions t 
                    LEFT JOIN users u ON t.user_id = u.user_id 
                    WHERE t.status = 'canceled'
                '''
                count_query = "SELECT COUNT(*) FROM transactions t WHERE t.status = 'canceled'"
                params = []
                param_count = 0
                
                if start_date:
                    param_count += 1
                    query += f" AND t.created_at >= ${param_count}"
                    count_query += f" AND t.created_at >= ${param_count}"
                    params.append(start_date)
                if end_date:
                    param_count += 1
                    query += f" AND t.created_at <= ${param_count}"
                    count_query += f" AND t.created_at <= ${param_count}"
                    params.append(end_date + ' 23:59:59')
                
                query += ' ORDER BY t.created_at DESC'
                
                records = await conn.fetch(query, *params)
                total_count = await conn.fetchval(count_query, *params)
                
                # Статистика
                refunds_query = "SELECT COALESCE(SUM(amount), 0) FROM transactions t WHERE t.status = 'canceled'"
                refunds_params = []
                param_count_refunds = 0
                if start_date:
                    param_count_refunds += 1
                    refunds_query += f" AND t.created_at >= ${param_count_refunds}"
                    refunds_params.append(start_date)
                if end_date:
                    param_count_refunds += 1
                    refunds_query += f" AND t.created_at <= ${param_count_refunds}"
                    refunds_params.append(end_date + ' 23:59:59')
                
                total_refunds = await conn.fetchval(refunds_query, *refunds_params)
                
                return {
                    'records': records,
                    'total_count': total_count,
                    'total_refunds': total_refunds,
                    'report_type': report_type,
                    'start_date': start_date,
                    'end_date': end_date
                }
                
            elif report_type == 'transactions':
                # Отчет по всем транзакциям
                query = '''
                    SELECT t.*, u.username, u.first_name 
                    FROM transactions t 
                    LEFT JOIN users u ON t.user_id = u.user_id 
                    WHERE 1=1
                '''
                count_query = 'SELECT COUNT(*) FROM transactions t WHERE 1=1'
                params = []
                param_count = 0
                
                if start_date:
                    param_count += 1
                    query += f" AND t.created_at >= ${param_count}"
                    count_query += f" AND t.created_at >= ${param_count}"
                    params.append(start_date)
                if end_date:
                    param_count += 1
                    query += f" AND t.created_at <= ${param_count}"
                    count_query += f" AND t.created_at <= ${param_count}"
                    params.append(end_date + ' 23:59:59')
                
                query += ' ORDER BY t.created_at DESC'
                
                records = await conn.fetch(query, *params)
                total_count = await conn.fetchval(count_query, *params)
                
                # Статистика по статусам
                status_stats = {}
                for status in ['pending', 'paid', 'canceled']:
                    status_query = f"SELECT COALESCE(SUM(amount), 0) FROM transactions t WHERE t.status = '{status}'"
                    status_params = []
                    param_count_status = 0
                    if start_date:
                        param_count_status += 1
                        status_query += f" AND t.created_at >= ${param_count_status}"
                        status_params.append(start_date)
                    if end_date:
                        param_count_status += 1
                        status_query += f" AND t.created_at <= ${param_count_status}"
                        status_params.append(end_date + ' 23:59:59')
                    
                    status_amount = await conn.fetchval(status_query, *status_params)
                    status_stats[status] = status_amount
                
                return {
                    'records': records,
                    'total_count': total_count,
                    'status_stats': status_stats,
                    'report_type': report_type,
                    'start_date': start_date,
                    'end_date': end_date
                }
    
    except Exception as e:
        logger.error(f"Error in accounting: {e}")
        return {
            'error': f'Ошибка загрузки данных: {e}',
            'records': [],
            'total_count': 0,
            'report_type': report_type,
            'start_date': start_date,
            'end_date': end_date
        }

@accounting_routes.get('/admin/accounting/export/excel')
async def export_accounting_excel(request):
    db_pool = request.app['db_pool']
    
    # Получаем параметры
    start_date = request.query.get('start_date')
    end_date = request.query.get('end_date')
    report_type = request.query.get('report_type', 'sales')
    
    try:
        async with db_pool.acquire() as conn:
            # Формируем запрос в зависимости от типа отчета
            if report_type == 'sales':
                query = '''
                    SELECT p.purchase_time, u.username, u.first_name, p.product, p.price, p.district, p.delivery_type
                    FROM purchases p 
                    LEFT JOIN users u ON p.user_id = u.user_id 
                    WHERE 1=1
                '''
                params = []
                param_count = 0
                
                if start_date:
                    param_count += 1
                    query += f" AND p.purchase_time >= ${param_count}"
                    params.append(start_date)
                if end_date:
                    param_count += 1
                    query += f" AND p.purchase_time <= ${param_count}"
                    params.append(end_date + ' 23:59:59')
                
                query += ' ORDER BY p.purchase_time DESC'
                
                records = await conn.fetch(query, *params)
                
                # Создаем CSV в памяти
                output = io.StringIO()
                writer = csv.writer(output)
                
                # Заголовки
                writer.writerow(['Дата', 'Пользователь', 'Имя', 'Товар', 'Цена', 'Район', 'Тип доставки'])
                
                # Данные
                for record in records:
                    writer.writerow([
                        record['purchase_time'].strftime('%Y-%m-%d %H:%M') if record['purchase_time'] else '',
                        record['username'] or '',
                        record['first_name'] or '',
                        record['product'] or '',
                        record['price'] or 0,
                        record['district'] or '',
                        record['delivery_type'] or ''
                    ])
                
                # Подготавливаем ответ
                response = web.StreamResponse()
                response.headers['Content-Type'] = 'text/csv'
                response.headers['Content-Disposition'] = f'attachment; filename="sales_report_{start_date}_{end_date}.csv"'
                
                await response.prepare(request)
                await response.write(output.getvalue().encode('utf-8'))
                await response.write_eof()
                
                return response
                
            elif report_type == 'refunds':
                query = '''
                    SELECT t.created_at, u.username, u.first_name, t.amount, t.currency, t.status, t.invoice_uuid
                    FROM transactions t 
                    LEFT JOIN users u ON t.user_id = u.user_id 
                    WHERE t.status = 'canceled'
                '''
                params = []
                param_count = 0
                
                if start_date:
                    param_count += 1
                    query += f" AND t.created_at >= ${param_count}"
                    params.append(start_date)
                if end_date:
                    param_count += 1
                    query += f" AND t.created_at <= ${param_count}"
                    params.append(end_date + ' 23:59:59')
                
                query += ' ORDER BY t.created_at DESC'
                
                records = await conn.fetch(query, *params)
                
                # Создаем CSV в памяти
                output = io.StringIO()
                writer = csv.writer(output)
                
                # Заголовки
                writer.writerow(['Дата', 'Пользователь', 'Имя', 'Сумма', 'Валюта', 'Статус', 'ID транзакции'])
                
                # Данные
                for record in records:
                    writer.writerow([
                        record['created_at'].strftime('%Y-%m-%d %H:%M') if record['created_at'] else '',
                        record['username'] or '',
                        record['first_name'] or '',
                        record['amount'] or 0,
                        record['currency'] or '',
                        record['status'] or '',
                        record['invoice_uuid'] or ''
                    ])
                
                # Подготавливаем ответ
                response = web.StreamResponse()
                response.headers['Content-Type'] = 'text/csv'
                response.headers['Content-Disposition'] = f'attachment; filename="refunds_report_{start_date}_{end_date}.csv"'
                
                await response.prepare(request)
                await response.write(output.getvalue().encode('utf-8'))
                await response.write_eof()
                
                return response
                
            elif report_type == 'transactions':
                query = '''
                    SELECT t.created_at, u.username, u.first_name, t.amount, t.currency, t.status, t.invoice_uuid
                    FROM transactions t 
                    LEFT JOIN users u ON t.user_id = u.user_id 
                    WHERE 1=1
                '''
                params = []
                param_count = 0
                
                if start_date:
                    param_count += 1
                    query += f" AND t.created_at >= ${param_count}"
                    params.append(start_date)
                if end_date:
                    param_count += 1
                    query += f" AND t.created_at <= ${param_count}"
                    params.append(end_date + ' 23:59:59')
                
                query += ' ORDER BY t.created_at DESC'
                
                records = await conn.fetch(query, *params)
                
                # Создаем CSV в памяти
                output = io.StringIO()
                writer = csv.writer(output)
                
                # Заголовки
                writer.writerow(['Дата', 'Пользователь', 'Имя', 'Сумма', 'Валюта', 'Статус', 'ID транзакции'])
                
                # Данные
                for record in records:
                    writer.writerow([
                        record['created_at'].strftime('%Y-%m-%d %H:%M') if record['created_at'] else '',
                        record['username'] or '',
                        record['first_name'] or '',
                        record['amount'] or 0,
                        record['currency'] or '',
                        record['status'] or '',
                        record['invoice_uuid'] or ''
                    ])
                
                # Подготавливаем ответ
                response = web.StreamResponse()
                response.headers['Content-Type'] = 'text/csv'
                response.headers['Content-Disposition'] = f'attachment; filename="transactions_report_{start_date}_{end_date}.csv"'
                
                await response.prepare(request)
                await response.write(output.getvalue().encode('utf-8'))
                await response.write_eof()
                
                return response
    
    except Exception as e:
        logger.error(f"Error in export_accounting_excel: {e}")
        return web.Response(text=f"Ошибка экспорта: {e}", status=500)

@accounting_routes.get('/admin/accounting/export/pdf')
async def export_accounting_pdf(request):
    db_pool = request.app['db_pool']
    
    # Получаем параметры
    start_date = request.query.get('start_date')
    end_date = request.query.get('end_date')
    report_type = request.query.get('report_type', 'sales')
    
    try:
        async with db_pool.acquire() as conn:
            # Формируем запрос в зависимости от типа отчета
            if report_type == 'sales':
                query = '''
                    SELECT p.purchase_time, u.username, u.first_name, p.product, p.price, p.district, p.delivery_type
                    FROM purchases p 
                    LEFT JOIN users u ON p.user_id = u.user_id 
                    WHERE 1=1
                '''
                params = []
                param_count = 0
                
                if start_date:
                    param_count += 1
                    query += f" AND p.purchase_time >= ${param_count}"
                    params.append(start_date)
                if end_date:
                    param_count += 1
                    query += f" AND p.purchase_time <= ${param_count}"
                    params.append(end_date + ' 23:59:59')
                
                query += ' ORDER BY p.purchase_time DESC'
                
                records = await conn.fetch(query, *params)
                
                # Создаем PDF в памяти
                buffer = io.BytesIO()
                doc = SimpleDocTemplate(buffer, pagesize=letter)
                elements = []
                
                # Заголовок
                styles = getSampleStyleSheet()
                title = Paragraph(f"Отчет по продажам: {start_date} - {end_date}", styles['Title'])
                elements.append(title)
                
                # Данные для таблицы
                data = [['Дата', 'Пользователь', 'Имя', 'Товар', 'Цена', 'Район', 'Тип доставки']]
                
                for record in records:
                    data.append([
                        record['purchase_time'].strftime('%Y-%m-%d %H:%M') if record['purchase_time'] else '',
                        record['username'] or '',
                        record['first_name'] or '',
                        record['product'] or '',
                        str(record['price'] or 0),
                        record['district'] or '',
                        record['delivery_type'] or ''
                    ])
                
                # Создаем таблицу
                table = Table(data)
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                    ('FONTSIZE', (0, 1), (-1, -1), 8),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black)
                ]))
                
                elements.append(table)
                
                # Строим PDF
                doc.build(elements)
                
                # Подготавливаем ответ
                response = web.StreamResponse()
                response.headers['Content-Type'] = 'application/pdf'
                response.headers['Content-Disposition'] = f'attachment; filename="sales_report_{start_date}_{end_date}.pdf"'
                
                await response.prepare(request)
                await response.write(buffer.getvalue())
                await response.write_eof()
                
                return response
                
            # Аналогично для других типов отчетов...
            # Код для refunds и transactions будет похожим
    
    except Exception as e:
        logger.error(f"Error in export_accounting_pdf: {e}")
        return web.Response(text=f"Ошибка экспорта: {e}", status=500)
