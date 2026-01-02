#!/usr/bin/env python3
"""
WSGI entry point for iCal Sync Service
Точка входа для запуска на хостинге с поддержкой WSGI
"""
import sys
import os
import logging

# Добавляем путь к проекту в sys.path
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

# Настраиваем логирование для WSGI
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(project_dir, 'data', 'logs', 'wsgi.log')),
        logging.StreamHandler(sys.stderr)  # Для хостингов, которые пишут в stderr
    ]
)

# Загружаем переменные окружения
from dotenv import load_dotenv
env_path = os.path.join(project_dir, '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
    print(f"WSGI: Загружена конфигурация из {env_path}", file=sys.stderr)
else:
    print(f"WSGI: Внимание: файл {env_path} не найден", file=sys.stderr)

# Импортируем основное приложение
try:
    from app import application
    print(f"WSGI: Приложение успешно загружено из {project_dir}", file=sys.stderr)
except Exception as e:
    print(f"WSGI: Ошибка загрузки приложения: {e}", file=sys.stderr)
    raise

# Обертка для приложения (опционально, для дополнительной обработки)
class WSGIApplicationWrapper:
    """Обертка для WSGI приложения с дополнительной обработкой"""
    
    def __init__(self, app):
        self.app = app
    
    def __call__(self, environ, start_response):
        # Логирование запросов
        if environ.get('REQUEST_METHOD') and environ.get('PATH_INFO'):
            print(
                f"WSGI Request: {environ['REQUEST_METHOD']} {environ['PATH_INFO']}",
                file=sys.stderr
            )
        
        # Добавляем информацию о проекте в заголовки
        def custom_start_response(status, headers, exc_info=None):
            headers.append(('X-Served-By', 'iCal-Sync-Service/1.0'))
            headers.append(('X-Project-Path', project_dir))
            return start_response(status, headers, exc_info)
        
        # Запускаем основное приложение
        return self.app(environ, custom_start_response)

# Создаем экземпляр приложения для WSGI сервера
application = WSGIApplicationWrapper(application)

# Для отладки: если файл запущен напрямую
if __name__ == '__main__':
    from wsgiref.simple_server import make_server
    
    # Читаем настройки из переменных окружения
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', '8000'))
    
    print(f"Запуск WSGI сервера на {host}:{port}")
    print(f"Проект: {project_dir}")
    
    try:
        httpd = make_server(host, port, application)
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен")