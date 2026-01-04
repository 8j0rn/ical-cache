#!/usr/bin/env python3
"""
index.wsgi - Упрощенная версия для отладки
"""
import sys
import os

# ============================================================================
# 1. БАЗОВАЯ НАСТРОЙКА
# ============================================================================
project_dir = os.path.dirname(os.path.abspath(__file__))

# Для отладки
sys.stderr.write("=" * 60 + "\n")
sys.stderr.write(f"[WSGI] Запуск из: {project_dir}\n")
sys.stderr.write(f"[WSGI] Python: {sys.executable}\n")

# ============================================================================
# 2. ВИРТУАЛЬНОЕ ОКРУЖЕНИЕ (.vbox или venv)
# ============================================================================
# Проверяем .vbox
vbox_path = os.path.join(project_dir, '.vbox')
if os.path.exists(vbox_path):
    sys.stderr.write(f"[WSGI] Найден .vbox: {vbox_path}\n")
    
    # Пытаемся добавить site-packages
    import glob
    site_pattern = os.path.join(vbox_path, 'lib', 'python*', 'site-packages')
    for site_path in glob.glob(site_pattern):
        if os.path.exists(site_path) and site_path not in sys.path:
            sys.path.insert(0, site_path)
            sys.stderr.write(f"[WSGI] Добавлен путь: {site_path}\n")

# ============================================================================
# 3. ПУТЬ ПРОЕКТА (как рекомендует хостинг)
# ============================================================================
if project_dir not in sys.path:
    sys.path.append(project_dir)
    sys.stderr.write(f"[WSGI] Добавлен проект: {project_dir}\n")

# ============================================================================
# 4. ПРОВЕРКА ИМПОРТОВ
# ============================================================================
sys.stderr.write("[WSGI] Проверка импортов...\n")
try:
    from dotenv import load_dotenv
    sys.stderr.write("[WSGI] ✅ dotenv импортирован\n")
    
    # Загружаем .env
    env_path = os.path.join(project_dir, '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
        sys.stderr.write(f"[WSGI] Загружен .env: {env_path}\n")
    else:
        sys.stderr.write(f"[WSGI] ⚠️ .env не найден\n")
        
except ImportError as e:
    sys.stderr.write(f"[WSGI] ❌ dotenv НЕ импортирован: {e}\n")

# ============================================================================
# 5. ИМПОРТ ПРИЛОЖЕНИЯ
# ============================================================================
sys.stderr.write("[WSGI] Импорт приложения...\n")
try:
    from app import application as original_app
    sys.stderr.write("[WSGI] ✅ Приложение импортировано\n")
except Exception as e:
    sys.stderr.write(f"[WSGI] ❌ Ошибка импорта: {e}\n")
    import traceback
    traceback.print_exc(file=sys.stderr)
    
    # Создаем простое приложение для отладки
    def debug_app(environ, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain; charset=utf-8')])
        
        output = []
        output.append("=== WSGI Debug ===")
        output.append(f"Python: {sys.executable}")
        output.append(f"Version: {sys.version}")
        output.append(f"Project: {project_dir}")
        output.append("")
        output.append("=== Environment ===")
        for key in ['PATH_INFO', 'SCRIPT_NAME', 'REQUEST_METHOD', 'QUERY_STRING']:
            output.append(f"{key}: {environ.get(key, '')}")
        output.append("")
        output.append("=== sys.path ===")
        for i, path in enumerate(sys.path[:10]):
            output.append(f"{i}: {path}")
        
        return ["\n".join(output).encode('utf-8')]
    
    original_app = debug_app

# ============================================================================
# 6. ОБЕРТКА ДЛЯ ОБРАБОТКИ ПУТЕЙ
# ============================================================================
def wsgi_app(environ, start_response):
    """Обертка для исправления путей"""
    
    # Логируем запрос
    path_info = environ.get('PATH_INFO', '')
    script_name = environ.get('SCRIPT_NAME', '')
    
    sys.stderr.write(f"[WSGI] Запрос: SCRIPT_NAME='{script_name}', PATH_INFO='{path_info}'\n")
    
    # Исправляем пустой PATH_INFO
    if not path_info or path_info == '':
        environ['PATH_INFO'] = '/'
        sys.stderr.write(f"[WSGI] Исправлен PATH_INFO: '/' -> '/'\n")
    
    # Убираем /index.wsgi из PATH_INFO если он там есть
    elif '/index.wsgi' in path_info:
        new_path = path_info.replace('/index.wsgi', '')
        if not new_path:
            new_path = '/'
        environ['PATH_INFO'] = new_path
        sys.stderr.write(f"[WSGI] Исправлен PATH_INFO: '{path_info}' -> '{new_path}'\n")
    
    # Запускаем оригинальное приложение
    return original_app(environ, start_response)

# Экспортируем обернутое приложение
application = wsgi_app

sys.stderr.write("[WSGI] Приложение готово\n")
sys.stderr.write("=" * 60 + "\n")

# ============================================================================
# 7. ЛОКАЛЬНЫЙ ЗАПУСК ДЛЯ ТЕСТА
# ============================================================================
if __name__ == '__main__':
    from wsgiref.simple_server import make_server
    print("Запуск на http://localhost:8000/")
    print("Откройте: http://localhost:8000/")
    print("         http://localhost:8000/health")
    httpd = make_server('localhost', 8000, application)
    httpd.serve_forever()