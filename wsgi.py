#!/usr/bin/env python3
"""
WSGI точка входа
"""
import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(__file__))

# Импортируем приложение
from app import application

# Для запуска через gunicorn/uWSGI
if __name__ == "__main__":
    # Локальный запуск для тестирования
    from wsgiref.simple_server import make_server
    
    host = '0.0.0.0'
    port = 8000
    
    print(f"Запуск сервера на {host}:{port}")
    httpd = make_server(host, port, application)
    httpd.serve_forever()
