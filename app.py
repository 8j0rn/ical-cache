#!/usr/bin/env python3
"""
WSGI приложение для iCal синхронизации с сессионной авторизацией
"""
import os
import sys
import json
import logging
import logging.handlers
import secrets
import pickle
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import unquote

import urllib.request
import ssl
import re

# Для отладки
print(f"[APP] Загрузка с сессионной авторизацией", file=sys.stderr)

# Загружаем переменные окружения из .env
from dotenv import load_dotenv

# Определяем путь к .env файлу
BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / '.env'

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
    print(f"[APP] Загружена конфигурация из {ENV_PATH}", file=sys.stderr)
else:
    print(f"[APP] Внимание: файл {ENV_PATH} не найден", file=sys.stderr)

# ============================================================================
# ГЛОБАЛЬНОЕ ХРАНИЛИЩЕ СЕССИЙ
# ============================================================================
class SessionStorage:
    """Глобальное хранилище сессий для всех экземпляров приложения"""
    _sessions = {}
    _session_file = BASE_DIR / 'data' / 'sessions.pkl'
    
    @classmethod
    def load_sessions(cls):
        """Загрузка сессий из файла"""
        try:
            if cls._session_file.exists():
                with open(cls._session_file, 'rb') as f:
                    cls._sessions = pickle.load(f)
                print(f"[SESSION] Загружено {len(cls._sessions)} сессий из файла", file=sys.stderr)
        except Exception as e:
            print(f"[SESSION] Ошибка загрузки сессий: {e}", file=sys.stderr)
            cls._sessions = {}
    
    @classmethod
    def save_sessions(cls):
        """Сохранение сессий в файл"""
        try:
            cls._session_file.parent.mkdir(exist_ok=True)
            with open(cls._session_file, 'wb') as f:
                pickle.dump(cls._sessions, f)
            print(f"[SESSION] Сохранено {len(cls._sessions)} сессий в файл", file=sys.stderr)
        except Exception as e:
            print(f"[SESSION] Ошибка сохранения сессий: {e}", file=sys.stderr)
    
    @classmethod
    def get(cls, session_id):
        """Получение сессии"""
        return cls._sessions.get(session_id)
    
    @classmethod
    def set(cls, session_id, data):
        """Сохранение сессии"""
        cls._sessions[session_id] = data
        cls.save_sessions()
    
    @classmethod
    def delete(cls, session_id):
        """Удаление сессии"""
        if session_id in cls._sessions:
            del cls._sessions[session_id]
            cls.save_sessions()
    
    @classmethod
    def cleanup_old_sessions(cls, timeout_seconds):
        """Очистка старых сессий"""
        cutoff = datetime.now() - timedelta(seconds=timeout_seconds)
        to_delete = []
        
        for session_id, session_data in cls._sessions.items():
            if session_data['last_activity'] < cutoff:
                to_delete.append(session_id)
        
        for session_id in to_delete:
            del cls._sessions[session_id]
        
        if to_delete:
            print(f"[SESSION] Очищено {len(to_delete)} старых сессий", file=sys.stderr)
            cls.save_sessions()

# Загружаем сессии при старте
SessionStorage.load_sessions()

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================
class Config:
    """Конфигурация приложения из .env"""
    
    # База данных
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = int(os.environ.get('DB_PORT', '3306'))
    DB_NAME = os.environ.get('DB_NAME', 'wordpress_db')
    DB_USER = os.environ.get('DB_USER', 'wordpress_user')
    DB_PASS = os.environ.get('DB_PASS', '')
    
    # WordPress
    WP_URL = os.environ.get('WP_URL', 'https://test.lakend.ru').rstrip('/')
    ICAL_ENDPOINT = os.environ.get('ICAL_ENDPOINT', '/ical-feed?ical=')
    
    # Аутентификация
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')
    SESSION_TIMEOUT = int(os.environ.get('SESSION_TIMEOUT', '86400'))  # 24 часа
    
    # Кэширование
    CACHE_MAX_AGE = int(os.environ.get('CACHE_MAX_AGE', '7200'))
    CACHE_CLEANUP_DAYS = int(os.environ.get('CACHE_CLEANUP_DAYS', '7'))
    
    # Публичные пути (без авторизации)
    @classmethod
    def get_public_paths(cls):
        """Получение публичных путей БЕЗ /admin"""
        public_paths_str = os.environ.get('PUBLIC_PATHS', '/ical/,/health/,/public/,/login,/logout')
        paths = [p.strip() for p in public_paths_str.split(',') if p.strip()]
        # Фильтруем /admin
        return [p for p in paths if p != '/admin' and not p.startswith('/admin')]
    
    # Логирование
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    LOG_FILE = os.environ.get('LOG_FILE', 'data/logs/app.log')
    
    # БАЗОВЫЙ ПУТЬ
    BASE_PATH = os.environ.get('BASE_PATH', '/8j0rn').rstrip('/')
    
    # Пути
    BASE_DIR = BASE_DIR
    DATA_DIR = BASE_DIR / 'data'
    CACHE_DIR = DATA_DIR / 'cache'
    LOGS_DIR = DATA_DIR / 'logs'
    
    @classmethod
    def setup_dirs(cls):
        """Создание необходимых директорий"""
        dirs = [cls.CACHE_DIR, cls.LOGS_DIR]
        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=True)
            print(f"[APP] Проверена директория: {directory}", file=sys.stderr)
    
    @classmethod
    def validate(cls):
        """Проверка конфигурации"""
        errors = []
        if not cls.DB_PASS:
            errors.append("DB_PASS не установлен")
        return errors

# Инициализация конфигурации
config = Config()
config.setup_dirs()

# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================
def setup_logging():
    """Настройка системы логирования"""
    logger = logging.getLogger('ical_sync')
    logger.setLevel(getattr(logging, config.LOG_LEVEL))
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    log_file = config.BASE_DIR / config.LOG_FILE
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Консольный обработчик для отладки
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

# ============================================================================
# БАЗА ДАННЫХ (упрощенная версия для теста)
# ============================================================================
class Database:
    """Упрощенное управление подключением к БД"""
    
    @staticmethod
    def get_connection():
        """Создание подключения к БД"""
        try:
            import MySQLdb
            conn = MySQLdb.connect(
                host=config.DB_HOST,
                port=config.DB_PORT,
                user=config.DB_USER,
                passwd=config.DB_PASS,
                db=config.DB_NAME,
                charset='utf8mb4',
                autocommit=True
            )
            return conn
        except Exception as e:
            logger.error(f"Ошибка подключения к БД: {e}")
            # Возвращаем None для теста
            return None
    
    @staticmethod
    def get_all_keys():
        """Получение всех iCal ключей"""
        try:
            conn = Database.get_connection()
            if not conn:
                return []
            
            cursor = conn.cursor()
            cursor.execute("""
                SELECT pm.meta_value, pm.post_id
                FROM wp_postmeta pm
                INNER JOIN wp_posts p ON pm.post_id = p.ID
                WHERE pm.meta_key = 'unique_code_ica'
                  AND pm.meta_value != ''
                  AND p.post_status = 'publish'
            """)
            
            results = cursor.fetchall()
            cursor.close()
            conn.close()
            
            logger.info(f"Найдено ключей: {len(results)}")
            return results
            
        except Exception as e:
            logger.error(f"Ошибка получения ключей: {e}")
            return []

# ============================================================================
# ICAL MANAGER (для синхронизации)
# ============================================================================
class ICalManager:
    """Менеджер для работы с iCal файлами"""
    
    def __init__(self):
        self.cache_dir = config.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def download_ical(self, ical_key, property_id=None):
        """Загрузка iCal файла по ключу"""
        import urllib.request
        import ssl
        
        try:
            # Формируем URL
            ical_url = f"{config.WP_URL}{config.ICAL_ENDPOINT}{ical_key}"
            logger.info(f"Загрузка iCal: {ical_url}")
            
            # Создаем контекст SSL (игнорируем ошибки для теста)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # Загружаем
            req = urllib.request.Request(
                ical_url,
                headers={'User-Agent': 'iCal-Sync/1.0'}
            )
            
            with urllib.request.urlopen(req, context=ssl_context, timeout=30) as response:
                content = response.read()
                
                # Проверяем, что это действительно iCal
                if b'BEGIN:VCALENDAR' in content or b'BEGIN:VCAL' in content:
                    logger.info(f"Успешно загружено: {len(content)} байт")
                    return content.decode('utf-8', errors='ignore')
                else:
                    logger.warning(f"Загруженный контент не похож на iCal: {content[:100]}")
                    return None
                    
        except Exception as e:
            logger.error(f"Ошибка загрузки iCal для ключа {ical_key}: {e}")
            return None
    
    def get_cache_filename(self, ical_key, property_id=None):
        """Получение имени файла для кэша"""
        import re
        if ical_key:
            # Очищаем ключ для использования в имени файла
            safe_key = re.sub(r'[^\w\-]', '_', str(ical_key))[:100]
            return f"{safe_key}.ics"
        elif property_id:
            return f"{property_id}.ics"
        return None
    
    def get_cache_path(self, ical_key, property_id=None):
        """Получение полного пути к файлу кэша"""
        filename = self.get_cache_filename(ical_key, property_id)
        if not filename:
            return None
        return self.cache_dir / filename
    
    def save_to_cache(self, ical_key, content, property_id=None):
        """Сохранение iCal в кэш"""
        try:
            cache_path = self.get_cache_path(ical_key, property_id)
            if not cache_path:
                return False
            
            cache_path.write_text(content, encoding='utf-8')
            logger.info(f"Сохранено в кэш: {cache_path.name}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка сохранения в кэш: {e}")
            return False
    
    def is_cache_valid(self, ical_key, property_id=None, max_age=None):
        """Проверка актуальности кэша"""
        if max_age is None:
            max_age = config.CACHE_MAX_AGE
        
        cache_path = self.get_cache_path(ical_key, property_id)
        if not cache_path or not cache_path.exists():
            return False
        
        import time
        file_age = time.time() - cache_path.stat().st_mtime
        return file_age < max_age
    
    def get_cached_content(self, ical_key, property_id=None):
        """Получение контента из кэша"""
        cache_path = self.get_cache_path(ical_key, property_id)
        if cache_path and cache_path.exists():
            try:
                return cache_path.read_text(encoding='utf-8')
            except Exception as e:
                logger.error(f"Ошибка чтения кэша: {e}")
        return None

# ============================================================================
# ОСНОВНОЕ ПРИЛОЖЕНИЕ С СЕССИОННОЙ АВТОРИЗАЦИЕЙ
# ============================================================================
class ICalApp:
    """Основное WSGI приложение с сессионной авторизацией"""
    
    def __init__(self):
        self.db = Database()
        self.public_paths = config.get_public_paths()
        logger.info(f"Приложение инициализировано. Публичные пути: {self.public_paths}")
    
    def _url(self, path):
        """Формирует URL с учетом BASE_PATH"""
        if not path.startswith('/'):
            path = '/' + path
        return config.BASE_PATH + path
    
    def is_public_path(self, path):
        """Проверяет, является ли путь публичным"""
        # Путь /admin НЕ должен быть публичным!
        if path == '/admin' or path.startswith('/admin/'):
            return False
            
        for public_path in self.public_paths:
            if path == public_path or path.startswith(public_path.rstrip('/') + '/'):
                return True
        
        return False
    
    def get_session_id(self, environ):
        """Извлечение ID сессии из куки"""
        cookies = environ.get('HTTP_COOKIE', '')
        
        for cookie in cookies.split(';'):
            cookie = cookie.strip()
            if cookie.startswith('ical_session='):
                return cookie.split('=', 1)[1]
        
        return None
    
    def validate_session(self, session_id):
        """Проверка валидности сессии"""
        if not session_id:
            logger.debug("Нет session_id для проверки")
            return False
        
        # Очищаем старые сессии
        SessionStorage.cleanup_old_sessions(config.SESSION_TIMEOUT)
        
        session_data = SessionStorage.get(session_id)
        if not session_data:
            logger.debug(f"Сессия {session_id[:8]}... не найдена в хранилище")
            return False
        
        # Обновляем время последней активности
        session_data['last_activity'] = datetime.now()
        SessionStorage.set(session_id, session_data)
        logger.debug(f"Сессия {session_id[:8]}... валидна")
        return True
    
    def create_session(self, username):
        """Создание новой сессии"""
        session_id = secrets.token_urlsafe(32)
        session_data = {
            'username': username,
            'created': datetime.now(),
            'last_activity': datetime.now()
        }
        SessionStorage.set(session_id, session_data)
        logger.info(f"Создана новая сессия для пользователя: {username}")
        return session_id
    
    def destroy_session(self, session_id):
        """Удаление сессии"""
        SessionStorage.delete(session_id)
        logger.info(f"Сессия {session_id[:8]}... удалена")
    
    def check_auth(self, environ):
        """Проверка авторизации"""
        # Проверяем куки
        cookies = environ.get('HTTP_COOKIE', '')
        logger.debug(f"Все куки: {cookies}")
        
        # Проверяем сессию
        session_id = self.get_session_id(environ)
        logger.debug(f"Извлеченный session_id: {session_id}")
        
        if session_id and self.validate_session(session_id):
            logger.info(f"Сессия валидна: {session_id[:8]}...")
            return True
        
        # Также проверяем REMOTE_USER (если есть Basic Auth на сервере)
        remote_user = environ.get('REMOTE_USER')
        if remote_user and remote_user == config.ADMIN_USERNAME:
            logger.info(f"Авторизация через REMOTE_USER: {remote_user}")
            return True
        
        logger.info(f"Авторизация не пройдена")
        return False
    
    def __call__(self, environ, start_response):
        """Основной обработчик WSGI"""
        path = environ.get('PATH_INFO', '/')
        
        # Нормализуем путь
        if not path:
            path = '/'
        elif not path.startswith('/'):
            path = '/' + path
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Новый запрос: {environ.get('REQUEST_METHOD')} {path}")
        logger.info(f"Клиент: {environ.get('REMOTE_ADDR', 'unknown')}")
        logger.info(f"User-Agent: {environ.get('HTTP_USER_AGENT', 'unknown')}")
        
        # Проверяем, публичный ли путь
        is_public = self.is_public_path(path)
        logger.info(f"Публичный путь: {'Да' if is_public else 'Нет'}")
        
        # Публичные пути доступны без авторизации
        if is_public:
            logger.info(f"Обработка публичного пути: {path}")
            return self.route_request(path, environ, start_response)
        
        logger.info(f"Защищенный путь: {path}")
        
        # Проверяем авторизацию для защищенных путей
        if not self.check_auth(environ):
            logger.warning(f"Доступ запрещен для: {path}")
            
            # Если это POST на /login - обрабатываем
            if path == '/login' and environ.get('REQUEST_METHOD') == 'POST':
                return self.handle_login_post(environ, start_response)
            
            # Показываем форму логина
            return self.show_login_form(path, environ, start_response)
        
        # Авторизация пройдена
        logger.info(f"Авторизация пройдена для: {path}")
        return self.route_request(path, environ, start_response)
    
    def route_request(self, path, environ, start_response):
        """Маршрутизация запросов"""
        method = environ.get('REQUEST_METHOD', 'GET')
        
        logger.debug(f"Маршрутизация: {method} {path}")
        
        if method == 'GET' and path.startswith('/ical/'):
            return self.handle_ical_request(path, environ, start_response)
        elif method == 'GET' and path == '/sync':
            return self.handle_sync(environ, start_response)
        elif method == 'GET' and path == '/admin':
            return self.handle_admin(environ, start_response)
        elif method == 'GET' and path == '/health':
            return self.handle_health(environ, start_response)
        elif method == 'GET' and path == '/login':
            return self.show_login_form(path, environ, start_response)
        elif method == 'GET' and path == '/logout':
            return self.handle_logout(environ, start_response)
        elif method == 'GET' and path in ['/', '/index.wsgi']:
            return self.handle_root(environ, start_response)
        elif method == 'POST' and path == '/login':
            return self.handle_login_post(environ, start_response)
        else:
            return self.handle_404(environ, start_response)
    
    def show_login_form(self, path, environ, start_response, error=None):
        """Показать форму логина"""
        redirect_to = path if path not in ['/login', '/'] else '/admin'
        if redirect_to.startswith('/login'):
            redirect_to = '/admin'
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Вход в админку</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 400px; margin: 100px auto; }}
                .login-box {{ 
                    border: 1px solid #ddd; 
                    padding: 30px; 
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                h2 {{ text-align: center; margin-bottom: 30px; color: #333; }}
                .form-group {{ margin-bottom: 20px; }}
                label {{ display: block; margin-bottom: 5px; font-weight: bold; }}
                input[type="text"],
                input[type="password"] {{
                    width: 100%;
                    padding: 12px;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    font-size: 16px;
                    box-sizing: border-box;
                }}
                button {{ 
                    width: 100%; 
                    padding: 12px; 
                    background: #4CAF50; 
                    color: white; 
                    border: none;
                    border-radius: 5px;
                    font-size: 16px;
                    cursor: pointer;
                }}
                button:hover {{ background: #45a049; }}
                .error {{ 
                    color: #d32f2f; 
                    background: #ffebee; 
                    padding: 10px; 
                    border-radius: 5px;
                    margin-bottom: 20px;
                }}
                .demo-credentials {{
                    margin-top: 20px;
                    padding: 15px;
                    background: #f5f5f5;
                    border-radius: 5px;
                    font-size: 14px;
                }}
            </style>
        </head>
        <body>
            <div class="login-box">
                <h2>Вход в админ-панель</h2>
                
                {'<div class="error">' + error + '</div>' if error else ''}
                
                <form method="post" action="{self._url('login')}">
                    <input type="hidden" name="redirect" value="{redirect_to}">
                    
                    <div class="form-group">
                        <label for="username">Логин:</label>
                        <input type="text" id="username" name="username" 
                               value="{config.ADMIN_USERNAME}" required>
                    </div>
                    
                    <div class="form-group">
                        <label for="password">Пароль:</label>
                        <input type="password" id="password" name="password" 
                               value="{config.ADMIN_PASSWORD}" required>
                    </div>
                    
                    <button type="submit">Войти</button>
                </form>
                
                <div class="demo-credentials">
                    <p><strong>Демо учетные данные:</strong></p>
                    <p>Логин: <code>{config.ADMIN_USERNAME}</code></p>
                    <p>Пароль: <code>{config.ADMIN_PASSWORD}</code></p>
                </div>
            </div>
        </body>
        </html>
        """
        
        headers = [('Content-Type', 'text/html; charset=utf-8')]
        start_response('200 OK', headers)
        return [html.encode('utf-8')]
    
    def handle_login_post(self, environ, start_response):
        """Обработка POST запроса на логин"""
        # Читаем POST данные
        content_length = int(environ.get('CONTENT_LENGTH', 0))
        if content_length:
            wsgi_input = environ.get('wsgi.input')
            post_data = wsgi_input.read(content_length).decode('utf-8')
            
            # Парсим параметры
            params = {}
            for pair in post_data.split('&'):
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    params[key] = unquote(value)
            
            username = params.get('username', '')
            password = params.get('password', '')
            redirect_to = params.get('redirect', '/admin')
            
            logger.info(f"Попытка входа: username={username}, password={'*' * len(password)}")
            logger.info(f"Редирект после входа: {redirect_to}")
            
            if username == config.ADMIN_USERNAME and password == config.ADMIN_PASSWORD:
                # Создаем сессию
                session_id = self.create_session(username)
                
                # Устанавливаем куку
                cookie_path = config.BASE_PATH or '/'
                headers = [
                    ('Set-Cookie', f'ical_session={session_id}; Path={cookie_path}; HttpOnly; SameSite=Lax'),
                    ('Location', self._url(redirect_to)),
                    ('Content-Type', 'text/html; charset=utf-8')
                ]
                start_response('302 Found', headers)
                logger.info(f"Успешный вход для пользователя: {username}, редирект на: {redirect_to}")
                return [b'']
            else:
                logger.warning(f"Неудачная попытка входа: {username}")
                return self.show_login_form('/login', environ, start_response, 
                                           "Неверный логин или пароль")
        
        return self.show_login_form('/login', environ, start_response, "Ошибка запроса")
    
    def handle_logout(self, environ, start_response):
        """Выход из системы"""
        session_id = self.get_session_id(environ)
        if session_id:
            self.destroy_session(session_id)
        
        # Удаляем куку
        cookie_path = config.BASE_PATH or '/'
        headers = [
            ('Set-Cookie', f'ical_session=; Path={cookie_path}; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly'),
            ('Location', self._url('/')),
            ('Content-Type', 'text/html; charset=utf-8')
        ]
        start_response('302 Found', headers)
        logger.info("Пользователь вышел из системы")
        return [b'']
    
    def handle_root(self, environ, start_response):
        """Главная страница"""
        is_authenticated = self.check_auth(environ)
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>iCal Sync Service</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }}
                .success {{ color: green; font-weight: bold; }}
                .warning {{ color: orange; font-weight: bold; }}
                .box {{ border: 1px solid #ddd; padding: 20px; border-radius: 10px; background: #f9f9f9; }}
                .auth-status {{ 
                    padding: 10px; 
                    margin-bottom: 20px; 
                    border-radius: 5px;
                    font-weight: bold;
                }}
                .authenticated {{ background: #d4edda; color: #155724; }}
                .not-authenticated {{ background: #f8d7da; color: #721c24; }}
                .btn {{ 
                    display: inline-block; 
                    padding: 10px 20px; 
                    margin: 5px;
                    text-decoration: none; 
                    border-radius: 5px; 
                    font-weight: bold;
                }}
                .btn-primary {{ background: #4CAF50; color: white; }}
                .btn-secondary {{ background: #6c757d; color: white; }}
                .btn-danger {{ background: #dc3545; color: white; }}
            </style>
        </head>
        <body>
            <div class="box">
                <h1>iCal Sync Service</h1>
                
                <div class="auth-status {'authenticated' if is_authenticated else 'not-authenticated'}">
                    Статус: {'✅ АВТОРИЗОВАН' if is_authenticated else '🚫 НЕ АВТОРИЗОВАН'}
                </div>
                
                <p>Система синхронизации iCal файлов для Авито.</p>
                
                <div style="margin: 20px 0;">
                    <a href="{self._url('health')}" class="btn btn-secondary">Health Check</a>
                    <a href="{self._url('admin')}" class="btn btn-primary">Admin Panel</a>
                    <a href="{self._url('ical/1.ics')}" class="btn btn-secondary">Пример iCal</a>
                    <a href="{self._url('sync')}" class="btn btn-primary">Sync</a>
                    {'<a href="' + self._url('logout') + '" class="btn btn-danger">Выйти</a>' if is_authenticated else '<a href="' + self._url('login') + '" class="btn btn-primary">Войти</a>'}
                </div>
                
                <p><small>Логин: <code>{config.ADMIN_USERNAME}</code><br>
                Пароль: <code>{config.ADMIN_PASSWORD}</code></small></p>
            </div>
        </body>
        </html>
        """
        
        headers = [('Content-Type', 'text/html; charset=utf-8')]
        start_response('200 OK', headers)
        return [html.encode('utf-8')]
    
    def handle_health(self, environ, start_response):
        """Health check (публичный)"""
        # Получаем количество сессий из хранилища
        from session_storage import session_storage
        sessions_count = len(session_storage._sessions)
        
        data = {
            'status': 'healthy',
            'service': 'iCal Sync',
            'timestamp': datetime.now().isoformat(),
            'auth': 'Session-based Auth',
            'public_paths': self.public_paths,
            'sessions_count': sessions_count
        }
        
        headers = [('Content-Type', 'application/json; charset=utf-8')]
        start_response('200 OK', headers)
        return [json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')]
    
    def handle_admin(self, environ, start_response):
        """Админ-панель (требует авторизации)"""
        # Дополнительная проверка
        if not self.check_auth(environ):
            return self.show_login_form('/admin', environ, start_response)
        
        # Получаем список ключей из БД
        keys = self.db.get_all_keys()
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>iCal Sync Admin</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }}
                header {{ background: #4CAF50; color: white; padding: 20px; border-radius: 10px; }}
                .stats {{ background: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
                th {{ background: #f2f2f2; }}
                .btn {{ 
                    display: inline-block; 
                    padding: 10px 20px; 
                    background: #4CAF50; 
                    color: white; 
                    text-decoration: none; 
                    border-radius: 5px; 
                    margin: 10px 5px;
                    border: none;
                    cursor: pointer;
                    font-size: 14px;
                }}
                .btn:hover {{ background: #45a049; }}
                .btn-secondary {{ background: #6c757d; }}
                .btn-secondary:hover {{ background: #5a6268; }}
                .btn-danger {{ background: #dc3545; }}
                .btn-danger:hover {{ background: #c82333; }}
            </style>
        </head>
        <body>
            <header>
                <h1>iCal Sync Admin Panel</h1>
                <p>Управление синхронизацией iCal файлов</p>
            </header>
            
            <div class="stats">
                <h3>Статистика</h3>
                <p>Найдено ключей в базе: <strong>{len(keys)}</strong></p>
                <p>Активных сессий: <strong>{len(SessionStorage._sessions)}</strong></p>
                <p>Логин: <code>{config.ADMIN_USERNAME}</code></p>
            </div>
            
            <div>
                <a href="{self._url('sync')}" class="btn">🔄 Синхронизировать все</a>
                <a href="{self._url('/')}" class="btn btn-secondary">🏠 На главную</a>
                <a href="{self._url('logout')}" class="btn btn-danger">🚪 Выйти</a>
            </div>
            
            {self._generate_keys_table(keys)}
            
            <footer style="margin-top: 40px; text-align: center; color: #666;">
                <p>iCal Sync Service • {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
            </footer>
        </body>
        </html>
        """
        
        headers = [('Content-Type', 'text/html; charset=utf-8')]
        start_response('200 OK', headers)
        return [html.encode('utf-8')]
    
    def _generate_keys_table(self, keys):
        """Генерация таблицы с ключами"""
        if not keys:
            return '<p>Нет ключей в базе данных</p>'
        
        rows = []
        for i, (ical_key, property_id) in enumerate(keys[:20], 1):  # Первые 20
            key_preview = ical_key[:20] + '...' if len(ical_key) > 20 else ical_key
            ical_url = self._url(f'ical/{property_id}.ics')
            
            rows.append(f"""
            <tr>
                <td>{i}</td>
                <td>{property_id}</td>
                <td><code>{key_preview}</code></td>
                <td><a href="{ical_url}" target="_blank">📥 Скачать iCal</a></td>
            </tr>
            """)
        
        return f"""
        <h3>Ключи iCal (первые 20)</h3>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>ID объекта</th>
                    <th>Ключ iCal</th>
                    <th>Действия</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
        """
    
    def handle_ical_request(self, path, environ, start_response):
        """Обработка запроса iCal файла"""
        # Извлекаем ID из пути
        filename = path.split('/')[-1]
        
        if not filename.endswith('.ics'):
            return self.handle_404(environ, start_response)
        
        # Простой тестовый iCal
        ical_content = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//iCal Sync//Production
BEGIN:VEVENT
UID:ical-sync-001
DTSTART:20240101T000000Z
DTEND:20240102T000000Z
SUMMARY:Test Event
DESCRIPTION:Событие из iCal Sync Service
END:VEVENT
END:VCALENDAR"""
        
        headers = [
            ('Content-Type', 'text/calendar; charset=utf-8'),
            ('Content-Disposition', f'inline; filename="{filename}"'),
            ('Cache-Control', 'public, max-age=300')
        ]
        start_response('200 OK', headers)
        return [ical_content.encode('utf-8')]
    
    def handle_sync(self, environ, start_response):
        """Синхронизация (требует авторизации)"""
        if not self.check_auth(environ):
            return self.show_login_form('/sync', environ, start_response)
        
        # Запускаем синхронизацию
        from sync import sync_keys
        
        try:
            # Запускаем синхронизацию (без ограничений)
            result = sync_keys()
            
            data = {
                'status': 'success',
                'message': 'Синхронизация выполнена',
                'result': result,
                'timestamp': datetime.now().isoformat(),
                'action': 'sync_all'
            }
            
            headers = [('Content-Type', 'application/json; charset=utf-8')]
            start_response('200 OK', headers)
            return [json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')]
            
        except Exception as e:
            logger.error(f"Ошибка синхронизации: {e}")
            
            data = {
                'status': 'error',
                'message': f'Ошибка синхронизации: {str(e)}',
                'timestamp': datetime.now().isoformat()
            }
            
            headers = [('Content-Type', 'application/json; charset=utf-8')]
            start_response('500 Internal Server Error', headers)
            return [json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')]
    
    def handle_404(self, environ, start_response, message="Not Found"):
        """Обработка 404 ошибки"""
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>404 Not Found</title>
            <meta charset="utf-8">
        </head>
        <body>
            <h1>404 Not Found</h1>
            <p>{message}</p>
            <p><a href="{self._url('/')}">На главную</a></p>
        </body>
        </html>
        """
        
        headers = [('Content-Type', 'text/html; charset=utf-8')]
        start_response('404 Not Found', headers)
        return [html.encode('utf-8')]

# ============================================================================
# Создание экземпляра приложения
# ============================================================================
application = ICalApp()

# ============================================================================
# Запуск для разработки
# ============================================================================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 iCal Sync Service с Сессионной Авторизацией")
    print(f"{'='*60}")
    print(f"📁 Директория:   {config.BASE_DIR}")
    print(f"🌐 URL:          {config.WP_URL}")
    print(f"🔐 Админ:        {config.ADMIN_USERNAME}:{config.ADMIN_PASSWORD}")
    print(f"📍 Базовый путь: {config.BASE_PATH}")
    print(f"⏱ Таймаут сессии: {config.SESSION_TIMEOUT} секунд")
    print(f"🌍 Публичные:    {config.get_public_paths()}")
    print(f"💾 Сессий в хранилище: {len(SessionStorage._sessions)}")
    print(f"{'='*60}")
    
    from wsgiref.simple_server import make_server
    httpd = make_server('0.0.0.0', 8000, application)
    print(f"\nСервер запущен: http://localhost:8000{config.BASE_PATH}")
    print(f"Публичные пути доступны без пароля")
    print(f"Защищенные пути требуют входа через форму")
    print(f"\nНажмите Ctrl+C для остановки")
    print("="*60 + "\n")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        # Сохраняем сессии перед выходом
        SessionStorage.save_sessions()
        print("\n👋 Сервер остановлен")