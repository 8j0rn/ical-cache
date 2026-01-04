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

import time

from ical_stats import ICalStats

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
        elif method == 'GET' and path.startswith('/sync_key/'):
            return self.handle_sync_single(environ, start_response)
        elif method == 'GET' and path.startswith('/delete_error/'):
            return self.handle_delete_error(environ, start_response)
        elif method == 'GET' and path == '/cleanup':
            return self.handle_cleanup(environ, start_response)
        elif method == 'GET' and path == '/stats':
            return self.handle_stats(environ, start_response)
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
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    margin: 0;
                    padding: 20px;
                }}
                .login-box {{ 
                    background: white;
                    padding: 40px;
                    border-radius: 15px;
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                    width: 100%;
                    max-width: 400px;
                }}
                h2 {{ 
                    text-align: center; 
                    margin-bottom: 30px; 
                    color: #333;
                    font-weight: 300;
                }}
                .logo {{
                    text-align: center;
                    font-size: 32px;
                    margin-bottom: 20px;
                    color: #764ba2;
                }}
                .form-group {{ margin-bottom: 25px; }}
                label {{ 
                    display: block; 
                    margin-bottom: 8px; 
                    font-weight: 500;
                    color: #555;
                }}
                input[type="text"],
                input[type="password"] {{
                    width: 100%;
                    padding: 14px;
                    border: 2px solid #e0e0e0;
                    border-radius: 8px;
                    font-size: 16px;
                    box-sizing: border-box;
                    transition: border-color 0.3s;
                }}
                input[type="text"]:focus,
                input[type="password"]:focus {{
                    outline: none;
                    border-color: #764ba2;
                    box-shadow: 0 0 0 3px rgba(118, 75, 162, 0.1);
                }}
                button {{ 
                    width: 100%; 
                    padding: 16px; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white; 
                    border: none;
                    border-radius: 8px;
                    font-size: 16px;
                    font-weight: 600;
                    cursor: pointer;
                    transition: transform 0.2s, box-shadow 0.2s;
                }}
                button:hover {{ 
                    transform: translateY(-2px);
                    box-shadow: 0 10px 20px rgba(118, 75, 162, 0.3);
                }}
                button:active {{ transform: translateY(0); }}
                
                .error {{ 
                    background: #fee;
                    color: #c33;
                    padding: 12px;
                    border-radius: 6px;
                    margin-bottom: 20px;
                    border-left: 4px solid #c33;
                    font-size: 14px;
                }}
                
                .forgot-link {{
                    text-align: center;
                    margin-top: 20px;
                    font-size: 14px;
                }}
                .forgot-link a {{
                    color: #764ba2;
                    text-decoration: none;
                }}
                .forgot-link a:hover {{ text-decoration: underline; }}
                
                .copyright {{
                    text-align: center;
                    margin-top: 30px;
                    color: #999;
                    font-size: 12px;
                }}
            </style>
        </head>
        <body>
            <div class="login-box">
                <div class="logo">🔐 iCal Sync</div>
                <h2>Вход в систему</h2>
                
                {'<div class="error">' + error + '</div>' if error else ''}
                
                <form method="post" action="{self._url('login')}">
                    <input type="hidden" name="redirect" value="{redirect_to}">
                    
                    <div class="form-group">
                        <label for="username">Имя пользователя</label>
                        <input type="text" id="username" name="username" 
                            placeholder="Введите логин" required autofocus>
                    </div>
                    
                    <div class="form-group">
                        <label for="password">Пароль</label>
                        <input type="password" id="password" name="password" 
                            placeholder="Введите пароль" required>
                    </div>
                    
                    <button type="submit">Войти в систему</button>
                </form>
                
                <div class="forgot-link">
                    <a href="#">Забыли пароль?</a>
                </div>
                
                <div class="copyright">
                    © 2024 iCal Sync Service
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
        
        # Получаем файлы из кэша
        cache_manager = ICalManager()
        cache_files = self._get_cache_files_info()
        error_files = self._get_error_files_info()
        
        # Получаем статистику
        stats = ICalStats.get_stats(days=7)
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>iCal Sync Admin</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 1400px; margin: 0 auto; padding: 20px; }}
                header {{ 
                    background: linear-gradient(135deg, #4CAF50, #45a049);
                    color: white; 
                    padding: 25px; 
                    border-radius: 10px;
                    margin-bottom: 20px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }}
                .stats-container {{ 
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                    gap: 15px;
                    margin: 20px 0;
                }}
                .stat-card {{
                    background: white;
                    padding: 20px;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                    border-left: 4px solid #4CAF50;
                }}
                .stat-card h4 {{ margin: 0 0 10px 0; color: #333; }}
                .stat-card .value {{ 
                    font-size: 24px; 
                    font-weight: bold; 
                    color: #4CAF50;
                    margin: 10px 0;
                }}
                .stat-card .label {{ color: #666; font-size: 14px; }}
                
                .config-box {{
                    background: #f8f9fa;
                    border: 1px solid #dee2e6;
                    border-radius: 8px;
                    padding: 20px;
                    margin: 20px 0;
                }}
                .config-row {{
                    display: flex;
                    justify-content: space-between;
                    margin: 10px 0;
                    padding: 8px 0;
                    border-bottom: 1px solid #eee;
                }}
                .config-label {{ font-weight: bold; color: #495057; }}
                .config-value {{ color: #6c757d; }}
                
                table {{ 
                    width: 100%; 
                    border-collapse: collapse; 
                    margin-top: 20px;
                    background: white;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                    border-radius: 8px;
                    overflow: hidden;
                }}
                th, td {{ 
                    padding: 12px 15px; 
                    text-align: left; 
                    border-bottom: 1px solid #dee2e6; 
                }}
                th {{ 
                    background: #f8f9fa; 
                    font-weight: 600;
                    color: #495057;
                    border-bottom: 2px solid #dee2e6;
                }}
                tr:hover {{ background: #f8f9fa; }}
                
                .status-badge {{
                    display: inline-block;
                    padding: 4px 8px;
                    border-radius: 12px;
                    font-size: 12px;
                    font-weight: bold;
                    text-transform: uppercase;
                }}
                .status-fresh {{ background: #d4edda; color: #155724; }}
                .status-stale {{ background: #fff3cd; color: #856404; }}
                .status-missing {{ background: #f8d7da; color: #721c24; }}
                
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
                    transition: all 0.3s;
                }}
                .btn:hover {{ 
                    background: #45a049; 
                    transform: translateY(-2px);
                    box-shadow: 0 4px 8px rgba(0,0,0,0.1);
                }}
                .btn-secondary {{ background: #6c757d; }}
                .btn-secondary:hover {{ background: #5a6268; }}
                .btn-danger {{ background: #dc3545; }}
                .btn-danger:hover {{ background: #c82333; }}
                .btn-info {{ background: #17a2b8; }}
                .btn-info:hover {{ background: #138496; }}
                
                .section-title {{
                    margin-top: 40px;
                    padding-bottom: 10px;
                    border-bottom: 2px solid #4CAF50;
                    color: #333;
                }}
                
                .tabs {{
                    display: flex;
                    border-bottom: 2px solid #dee2e6;
                    margin: 30px 0 20px 0;
                }}
                .tab {{
                    padding: 10px 20px;
                    cursor: pointer;
                    border-bottom: 3px solid transparent;
                    margin-bottom: -2px;
                }}
                .tab.active {{
                    border-bottom-color: #4CAF50;
                    font-weight: bold;
                    color: #4CAF50;
                }}
                
                .tab-content {{
                    display: none;
                }}
                .tab-content.active {{
                    display: block;
                }}
                
                .file-size {{ color: #6c757d; font-size: 12px; }}
                .timestamp {{ color: #6c757d; font-size: 12px; }}
                
                .actions-cell {{ white-space: nowrap; }}
                .actions-cell a {{
                    margin: 0 5px;
                    color: #4CAF50;
                    text-decoration: none;
                }}
                .actions-cell a:hover {{ text-decoration: underline; }}
                
                footer {{ 
                    margin-top: 40px; 
                    text-align: center; 
                    color: #666;
                    padding-top: 20px;
                    border-top: 1px solid #dee2e6;
                }}
            </style>
            <script>
                function showTab(tabName) {{
                    // Скрыть все вкладки
                    var tabContents = document.querySelectorAll('.tab-content');
                    for (var i = 0; i < tabContents.length; i++) {{
                        tabContents[i].style.display = 'none';
                        tabContents[i].classList.remove('active');
                    }}
                    
                    var tabs = document.querySelectorAll('.tab');
                    for (var i = 0; i < tabs.length; i++) {{
                        tabs[i].classList.remove('active');
                    }}
                    
                    // Показать выбранную вкладку
                    var selectedTab = document.getElementById(tabName + '-tab');
                    var selectedContent = document.getElementById(tabName + '-content');
                    
                    if (selectedTab) {{
                        selectedTab.classList.add('active');
                    }}
                    
                    if (selectedContent) {{
                        selectedContent.style.display = 'block';
                        selectedContent.classList.add('active');
                    }}
                }}
                
                function confirmSync() {{
                    if (confirm('Запустить синхронизацию всех iCal файлов? Это может занять несколько минут.')) {{
                        window.location.href = '{self._url('sync')}';
                    }}
                }}
                
                function confirmCleanup() {{
                    if (confirm('Очистить старые кэш-файлы? Будут удалены файлы старше {config.CACHE_CLEANUP_DAYS} дней.')) {{
                        window.location.href = '{self._url('cleanup')}';
                    }}
                }}
            </script>
        </head>
        <body>
            <header>
                <h1>📊 iCal Sync Admin Panel</h1>
                <p>Управление синхронизацией iCal файлов для Авито</p>
            </header>
            
            <div class="stats-container">
                <div class="stat-card">
                    <h4>📁 Ключи в БД</h4>
                    <div class="value">{len(keys)}</div>
                    <div class="label">Опубликованных объектов</div>
                </div>
                
                <div class="stat-card">
                    <h4>✅ Файлов iCal</h4>
                    <div class="value">{len(cache_files)}</div>
                    <div class="label">Сохранено в кэше</div>
                </div>
                
                <div class="stat-card">
                    <h4>⚠ Файлов ошибок</h4>
                    <div class="value">{len(error_files)}</div>
                    <div class="label">Ошибки загрузки</div>
                </div>
                
                <div class="stat-card">
                    <h4>📈 Запросов iCal</h4>
                    <div class="value">{stats.get('total_requests', 0)}</div>
                    <div class="label">За последние 7 дней</div>
                </div>
            </div>
            
            <div class="config-box">
                <h3>⚙️ Конфигурация</h3>
                <div class="config-row">
                    <span class="config-label">Время жизни кэша:</span>
                    <span class="config-value">{config.CACHE_MAX_AGE // 3600} часов</span>
                </div>
                <div class="config-row">
                    <span class="config-label">Очистка старых файлов:</span>
                    <span class="config-value">через {config.CACHE_CLEANUP_DAYS} дней</span>
                </div>
                <div class="config-row">
                    <span class="config-label">База данных:</span>
                    <span class="config-value">{config.DB_NAME} ({config.DB_HOST})</span>
                </div>
                <div class="config-row">
                    <span class="config-label">WordPress URL:</span>
                    <span class="config-value">{config.WP_URL}</span>
                </div>
            </div>
            
            <div>
                <button onclick="confirmSync()" class="btn">🔄 Синхронизировать все</button>
                <button onclick="confirmCleanup()" class="btn btn-secondary">🧹 Очистить старый кэш</button>
                <a href="{self._url('stats')}" class="btn btn-info">📊 Статистика запросов</a>
                <a href="{self._url('/')}" class="btn btn-secondary">🏠 На главную</a>
                <a href="{self._url('logout')}" class="btn btn-danger">🚪 Выйти</a>
            </div>
            
            <div class="tabs">
                <div id="keys-tab" class="tab active" onclick="showTab('keys')">Ключи из БД</div>
                <div id="cache-tab" class="tab" onclick="showTab('cache')">Файлы в кэше</div>
                <div id="errors-tab" class="tab" onclick="showTab('errors')">Файлы ошибок</div>
            </div>

            <div id="keys-content" class="tab-content active">
                <h3 class="section-title">Ключи iCal из базы данных</h3>
                {self._generate_keys_table(keys)}
            </div>

            <div id="cache-content" class="tab-content" style="display: none;">
                <h3 class="section-title">Файлы iCal в кэше</h3>
                {self._generate_cache_table(cache_files)}
            </div>

            <div id="errors-content" class="tab-content" style="display: none;">
                <h3 class="section-title">Файлы ошибок</h3>
                {self._generate_errors_table(error_files)}
            </div>
            
            <footer>
                <p>iCal Sync Service • {datetime.now().strftime('%Y-%m-%d %H:%M')} • Активных сессий: {len(SessionStorage._sessions)}</p>
            </footer>
            
            <script>
                // Показываем первую вкладку по умолчанию
                // showTab('keys');
            </script>
        </body>
        </html>
        """
        
        headers = [('Content-Type', 'text/html; charset=utf-8')]
        start_response('200 OK', headers)
        return [html.encode('utf-8')]

    def _get_cache_files_info(self):
        """Получение информации о файлах в кэше"""
        cache_manager = ICalManager()
        cache_dir = Path(config.CACHE_DIR)
        
        files_info = []
        if cache_dir.exists():
            for ics_file in cache_dir.glob("*.ics"):
                stat = ics_file.stat()
                file_age = time.time() - stat.st_mtime
                is_fresh = file_age < config.CACHE_MAX_AGE
                
                files_info.append({
                    'filename': ics_file.name,
                    'size_kb': stat.st_size / 1024,
                    'modified': datetime.fromtimestamp(stat.st_mtime),
                    'age_seconds': file_age,
                    'is_fresh': is_fresh,
                    'path': ics_file
                })
        
        # Сортируем по времени изменения (новые первыми)
        files_info.sort(key=lambda x: x['modified'], reverse=True)
        return files_info

    def _get_error_files_info(self):
        """Получение информации о файлах ошибок"""
        cache_dir = Path(config.CACHE_DIR)
        
        files_info = []
        if cache_dir.exists():
            for err_file in cache_dir.glob("*.err"):
                stat = err_file.stat()
                
                files_info.append({
                    'filename': err_file.name,
                    'size_kb': stat.st_size / 1024,
                    'modified': datetime.fromtimestamp(stat.st_mtime),
                    'path': err_file
                })
        
        # Сортируем по времени изменения (новые первыми)
        files_info.sort(key=lambda x: x['modified'], reverse=True)
        return files_info

    def _generate_cache_table(self, cache_files):
        """Генерация таблицы с файлами кэша"""
        if not cache_files:
            return '<p>Нет файлов в кэше</p>'
        
        rows = []
        for i, file_info in enumerate(cache_files[:50], 1):  # Первые 50
            status_class = 'status-fresh' if file_info['is_fresh'] else 'status-stale'
            status_text = 'Актуален' if file_info['is_fresh'] else 'Устарел'
            
            filename = file_info['filename']
            size_kb = file_info['size_kb']
            modified = file_info['modified'].strftime('%Y-%m-%d %H:%M')
            age_hours = int(file_info['age_seconds'] // 3600)
            
            ical_url = self._url(f'ical/{filename}')
            ical_key = filename[:-4]  # Убираем .ics
            
            rows.append(f"""
            <tr>
                <td>{i}</td>
                <td><code>{filename}</code></td>
                <td><span class="status-badge {status_class}">{status_text}</span></td>
                <td>{size_kb:.1f} KB</td>
                <td>
                    <div>{modified}</div>
                    <div class="timestamp">({age_hours} часов назад)</div>
                </td>
                <td class="actions-cell">
                    <a href="{ical_url}" target="_blank">📥 Скачать</a>
                    <a href="{self._url(f'sync_key/{ical_key}')}" title="Обновить">🔄</a>
                </td>
            </tr>
            """)
        
        return f"""
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Имя файла</th>
                    <th>Статус</th>
                    <th>Размер</th>
                    <th>Изменен</th>
                    <th>Действия</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
        <p style="color: #666; margin-top: 10px;">Показано {len(cache_files[:50])} из {len(cache_files)} файлов</p>
        """

    def _generate_errors_table(self, error_files):
        """Генерация таблицы с файлами ошибок"""
        if not error_files:
            return '<p>Файлов ошибок нет</p>'
        
        rows = []
        for i, file_info in enumerate(error_files[:30], 1):  # Первые 30
            filename = file_info['filename']
            size_kb = file_info['size_kb']
            modified = file_info['modified'].strftime('%Y-%m-%d %H:%M')
            
            # Пробуем прочитать содержимое ошибки
            error_content = ""
            try:
                error_content = file_info['path'].read_text(encoding='utf-8')[:100]
                if len(error_content) > 100:
                    error_content = error_content[:97] + "..."
            except:
                error_content = "Не удалось прочитать"
            
            rows.append(f"""
            <tr>
                <td>{i}</td>
                <td><code>{filename}</code></td>
                <td>{size_kb:.1f} KB</td>
                <td>{modified}</td>
                <td><span style="color: #dc3545; font-family: monospace;">{error_content}</span></td>
                <td class="actions-cell">
                    <a href="{self._url(f'delete_error/{filename}')}" title="Удалить" style="color: #dc3545;">🗑️</a>
                </td>
            </tr>
            """)
        
        return f"""
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Имя файла</th>
                    <th>Размер</th>
                    <th>Создан</th>
                    <th>Ошибка</th>
                    <th>Действия</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
        <p style="color: #666; margin-top: 10px;">Показано {len(error_files[:30])} из {len(error_files)} файлов</p>
        """
    
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
        
        # Логируем запрос
        client_ip = environ.get('REMOTE_ADDR', 'unknown')
        user_agent = environ.get('HTTP_USER_AGENT', 'unknown')
        ICalStats.log_request(filename, client_ip, user_agent)
        
        # Проверяем, есть ли файл в кэше
        cache_manager = ICalManager()
        
        # Пробуем извлечь ключ из имени файла (формат: ключ.ics или ID.ics)
        import re
        ical_key = None
        property_id = None
        
        # Если имя файла - число, это ID
        if filename[:-4].isdigit():
            property_id = int(filename[:-4])
            # Ищем ключ в базе
            try:
                conn = self.db.get_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT meta_value FROM wp_postmeta 
                        WHERE post_id = %s AND meta_key = 'unique_code_ica' 
                        AND meta_value != ''
                    """, (property_id,))
                    result = cursor.fetchone()
                    if result:
                        ical_key = result[0]
                    cursor.close()
                    conn.close()
            except:
                pass
        else:
            # Это ключ
            ical_key = filename[:-4]
        
        # Пробуем получить из кэша
        cached_content = cache_manager.get_cached_content(ical_key, property_id)
        
        if cached_content:
            # Файл в кэше
            ical_content = cached_content
            logger.info(f"Отправлен кэшированный файл: {filename}")
        else:
            # Генерируем тестовый файл
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
            logger.info(f"Отправлен тестовый файл: {filename}")
        
        headers = [
            ('Content-Type', 'text/calendar; charset=utf-8'),
            ('Content-Disposition', f'inline; filename="{filename}"'),
            ('Cache-Control', 'public, max-age=300')
        ]
        start_response('200 OK', headers)
        return [ical_content.encode('utf-8')]
        
    def handle_sync(self, environ, start_response):
        """HTML страница синхронизации"""
        if not self.check_auth(environ):
            return self.show_login_form('/sync', environ, start_response)
        
        # Запускаем синхронизацию
        from sync import sync_keys
        
        try:
            # Запускаем синхронизацию (без ограничений)
            result = sync_keys()
            
            # Форматируем результат для отображения
            result_html = ""
            if result.get('total', 0) > 0:
                success_pct = (result['success'] / result['total']) * 100 if result['total'] > 0 else 0
                
                result_html = f"""
                <div class="result-box success">
                    <h3>✅ Синхронизация завершена!</h3>
                    <div class="result-stats">
                        <div class="stat">
                            <span class="label">Всего объектов:</span>
                            <span class="value">{result['total']}</span>
                        </div>
                        <div class="stat">
                            <span class="label">Успешно:</span>
                            <span class="value" style="color: #28a745;">{result['success']}</span>
                        </div>
                        <div class="stat">
                            <span class="label">Пропущено (актуальны):</span>
                            <span class="value" style="color: #ffc107;">{result['skipped']}</span>
                        </div>
                        <div class="stat">
                            <span class="label">Ошибки:</span>
                            <span class="value" style="color: #dc3545;">{result['errors']}</span>
                        </div>
                        <div class="stat">
                            <span class="label">Успешность:</span>
                            <span class="value">{success_pct:.1f}%</span>
                        </div>
                    </div>
                    
                    <div style="margin-top: 20px;">
                        <h4>Детали выполнения:</h4>
                        <pre style="background: #f8f9fa; padding: 15px; border-radius: 5px; max-height: 300px; overflow: auto;">
{json.dumps(result, indent=2, ensure_ascii=False)}
                        </pre>
                    </div>
                </div>
                """
            else:
                result_html = """
                <div class="result-box warning">
                    <h3>⚠️ Нет объектов для синхронизации</h3>
                    <p>В базе данных не найдено объектов с iCal ключами.</p>
                </div>
                """
            
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Результат синхронизации</title>
                <meta charset="utf-8">
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }}
                    .box {{ 
                        border: 1px solid #ddd; 
                        padding: 30px; 
                        border-radius: 10px;
                        background: #f9f9f9;
                        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    }}
                    h1 {{ color: #333; margin-bottom: 30px; }}
                    
                    .result-box {{
                        margin: 30px 0;
                        padding: 25px;
                        border-radius: 8px;
                        background: white;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                    }}
                    .result-box.success {{ border-left: 5px solid #28a745; }}
                    .result-box.warning {{ border-left: 5px solid #ffc107; }}
                    .result-box.error {{ border-left: 5px solid #dc3545; }}
                    
                    .result-stats {{
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                        gap: 15px;
                        margin: 20px 0;
                    }}
                    .stat {{
                        background: #f8f9fa;
                        padding: 15px;
                        border-radius: 5px;
                    }}
                    .stat .label {{
                        display: block;
                        font-size: 14px;
                        color: #6c757d;
                        margin-bottom: 5px;
                    }}
                    .stat .value {{
                        display: block;
                        font-size: 24px;
                        font-weight: bold;
                        color: #333;
                    }}
                    
                    .btn {{ 
                        display: inline-block; 
                        padding: 12px 24px; 
                        background: #4CAF50; 
                        color: white; 
                        text-decoration: none; 
                        border-radius: 5px; 
                        margin: 10px 5px;
                        border: none;
                        cursor: pointer;
                        font-size: 16px;
                    }}
                    .btn:hover {{ background: #45a049; }}
                    .btn-secondary {{ background: #6c757d; }}
                    .btn-secondary:hover {{ background: #5a6268; }}
                    
                    .timestamp {{
                        color: #6c757d;
                        font-size: 14px;
                        margin-top: 20px;
                        padding-top: 20px;
                        border-top: 1px solid #dee2e6;
                    }}
                </style>
            </head>
            <body>
                <div class="box">
                    <h1>🔄 Результат синхронизации iCal</h1>
                    
                    {result_html}
                    
                    <div style="margin-top: 30px;">
                        <a href="{self._url('admin')}" class="btn">← Назад в админку</a>
                        <a href="{self._url('sync')}" class="btn-secondary">🔄 Запустить снова</a>
                        <a href="{self._url('/')}" class="btn-secondary">🏠 На главную</a>
                    </div>
                    
                    <div class="timestamp">
                        Время выполнения: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    </div>
                </div>
            </body>
            </html>
            """
            
            headers = [('Content-Type', 'text/html; charset=utf-8')]
            start_response('200 OK', headers)
            return [html.encode('utf-8')]
            
        except Exception as e:
            logger.error(f"Ошибка синхронизации: {e}")
            
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Ошибка синхронизации</title>
                <meta charset="utf-8">
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }}
                    .box {{ 
                        border: 1px solid #ddd; 
                        padding: 30px; 
                        border-radius: 10px;
                        background: #f9f9f9;
                    }}
                    .error-box {{
                        background: #f8d7da;
                        color: #721c24;
                        padding: 20px;
                        border-radius: 5px;
                        margin: 20px 0;
                    }}
                    .btn {{ 
                        display: inline-block; 
                        padding: 10px 20px; 
                        background: #6c757d; 
                        color: white; 
                        text-decoration: none; 
                        border-radius: 5px; 
                        margin: 10px 5px;
                    }}
                    .btn:hover {{ background: #5a6268; }}
                </style>
            </head>
            <body>
                <div class="box">
                    <h1>❌ Ошибка синхронизации</h1>
                    
                    <div class="error-box">
                        <h3>Произошла ошибка:</h3>
                        <pre>{str(e)}</pre>
                    </div>
                    
                    <div>
                        <a href="{self._url('admin')}" class="btn">← Назад в админку</a>
                        <a href="{self._url('/')}" class="btn">🏠 На главную</a>
                    </div>
                </div>
            </body>
            </html>
            """
            
            headers = [('Content-Type', 'text/html; charset=utf-8')]
            start_response('500 Internal Server Error', headers)
            return [html.encode('utf-8')]

    def handle_stats(self, environ, start_response):
        """Страница статистики запросов"""
        if not self.check_auth(environ):
            return self.show_login_form('/stats', environ, start_response)
        
        # Получаем статистику
        stats = ICalStats.get_stats(days=30)
        
        # Формируем таблицу топ файлов
        top_files_html = ""
        if stats.get('by_filename'):
            top_files = sorted(stats['by_filename'].items(), key=lambda x: x[1], reverse=True)[:20]
            
            rows = []
            for i, (filename, count) in enumerate(top_files, 1):
                ical_url = self._url(f'ical/{filename}')
                rows.append(f"""
                <tr>
                    <td>{i}</td>
                    <td><code>{filename}</code></td>
                    <td>{count}</td>
                    <td><a href="{ical_url}" target="_blank">Ссылка</a></td>
                </tr>
                """)
            
            top_files_html = f"""
            <h3>📊 Топ-20 запрашиваемых файлов</h3>
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Файл</th>
                        <th>Запросов</th>
                        <th>Ссылка</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows)}
                </tbody>
            </table>
            """
        
        # Формируем таблицу последних запросов
        recent_requests_html = ""
        if stats.get('recent_requests'):
            rows = []
            for i, req in enumerate(stats['recent_requests'][:20], 1):
                dt = datetime.fromtimestamp(req['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
                rows.append(f"""
                <tr>
                    <td>{i}</td>
                    <td>{dt}</td>
                    <td><code>{req['filename']}</code></td>
                    <td>{req['client_ip']}</td>
                    <td><span class="status-badge {'status-fresh' if req['status'] == 'success' else 'status-stale'}">{req['status']}</span></td>
                </tr>
                """)
            
            recent_requests_html = f"""
            <h3>🕒 Последние 20 запросов</h3>
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Время</th>
                        <th>Файл</th>
                        <th>IP</th>
                        <th>Статус</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows)}
                </tbody>
            </table>
            """
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Статистика запросов iCal</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }}
                header {{ 
                    background: linear-gradient(135deg, #17a2b8, #138496);
                    color: white; 
                    padding: 25px; 
                    border-radius: 10px;
                    margin-bottom: 20px;
                }}
                .stats-container {{ 
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                    gap: 15px;
                    margin: 20px 0;
                }}
                .stat-card {{
                    background: white;
                    padding: 20px;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                    border-left: 4px solid #17a2b8;
                }}
                .stat-card h4 {{ margin: 0 0 10px 0; color: #333; }}
                .stat-card .value {{ 
                    font-size: 24px; 
                    font-weight: bold; 
                    color: #17a2b8;
                    margin: 10px 0;
                }}
                .stat-card .label {{ color: #666; font-size: 14px; }}
                
                table {{ 
                    width: 100%; 
                    border-collapse: collapse; 
                    margin: 20px 0;
                    background: white;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                    border-radius: 8px;
                    overflow: hidden;
                }}
                th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #dee2e6; }}
                th {{ background: #f8f9fa; font-weight: 600; color: #495057; }}
                tr:hover {{ background: #f8f9fa; }}
                
                .btn {{ 
                    display: inline-block; 
                    padding: 10px 20px; 
                    background: #17a2b8; 
                    color: white; 
                    text-decoration: none; 
                    border-radius: 5px; 
                    margin: 10px 5px;
                    border: none;
                    cursor: pointer;
                }}
                .btn:hover {{ background: #138496; }}
                .btn-secondary {{ background: #6c757d; }}
                .btn-secondary:hover {{ background: #5a6268; }}
                
                .status-badge {{
                    display: inline-block;
                    padding: 4px 8px;
                    border-radius: 12px;
                    font-size: 12px;
                    font-weight: bold;
                    text-transform: uppercase;
                }}
                .status-fresh {{ background: #d4edda; color: #155724; }}
                .status-stale {{ background: #fff3cd; color: #856404; }}
            </style>
        </head>
        <body>
            <header>
                <h1>📈 Статистика запросов iCal файлов</h1>
                <p>Аналитика запросов за последние 30 дней</p>
            </header>
            
            <div class="stats-container">
                <div class="stat-card">
                    <h4>📊 Всего запросов</h4>
                    <div class="value">{stats.get('total_requests', 0)}</div>
                    <div class="label">За 30 дней</div>
                </div>
                
                <div class="stat-card">
                    <h4>✅ Успешных</h4>
                    <div class="value">{stats.get('successful', 0)}</div>
                    <div class="label">Завершено успешно</div>
                </div>
                
                <div class="stat-card">
                    <h4>⚠ С ошибками</h4>
                    <div class="value">{stats.get('failed', 0)}</div>
                    <div class="label">Завершено с ошибкой</div>
                </div>
                
                <div class="stat-card">
                    <h4>🌐 Уникальных IP</h4>
                    <div class="value">{len(stats.get('by_ip', {}))}</div>
                    <div class="label">Разных адресов</div>
                </div>
            </div>
            
            <div>
                <a href="{self._url('admin')}" class="btn-secondary btn">← Назад в админку</a>
                <a href="{self._url('/')}" class="btn-secondary btn">🏠 На главную</a>
            </div>
            
            {top_files_html}
            {recent_requests_html}
            
            <footer style="margin-top: 40px; text-align: center; color: #666;">
                <p>Статистика обновлена: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            </footer>
        </body>
        </html>
        """
        
        headers = [('Content-Type', 'text/html; charset=utf-8')]
        start_response('200 OK', headers)
        return [html.encode('utf-8')]
    
    def handle_sync_single(self, environ, start_response):
        """Синхронизация одного ключа"""
        if not self.check_auth(environ):
            return self.show_login_form('/admin', environ, start_response)
        
        path = environ.get('PATH_INFO', '')
        key = path.split('/sync_key/')[-1]
        
        # TODO: Реализовать синхронизацию одного ключа
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Синхронизация ключа</title></head>
        <body>
            <h1>Синхронизация ключа: {key}</h1>
            <p>Функция в разработке...</p>
            <a href="{self._url('admin')}">← Назад</a>
        </body>
        </html>
        """
        
        headers = [('Content-Type', 'text/html; charset=utf-8')]
        start_response('200 OK', headers)
        return [html.encode('utf-8')]

    def handle_delete_error(self, environ, start_response):
        """Удаление файла ошибки"""
        if not self.check_auth(environ):
            return self.show_login_form('/admin', environ, start_response)
        
        path = environ.get('PATH_INFO', '')
        filename = path.split('/delete_error/')[-1]
        
        cache_dir = Path(config.CACHE_DIR)
        error_file = cache_dir / filename
        
        if error_file.exists() and filename.endswith('.err'):
            try:
                error_file.unlink()
                logger.info(f"Удален файл ошибки: {filename}")
            except Exception as e:
                logger.error(f"Ошибка удаления файла {filename}: {e}")
        
        # Редирект обратно в админку
        headers = [
            ('Location', self._url('admin')),
            ('Content-Type', 'text/html; charset=utf-8')
        ]
        start_response('302 Found', headers)
        return [b'']

    def handle_cleanup(self, environ, start_response):
        """Очистка старых файлов"""
        if not self.check_auth(environ):
            return self.show_login_form('/admin', environ, start_response)
        
        from cleanup import cleanup_old_files
        result = cleanup_old_files()
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Очистка кэша</title>
            <style>
                body {{ font-family: Arial; padding: 50px; }}
                .box {{ max-width: 600px; margin: auto; padding: 30px; border: 1px solid #ddd; }}
                .success {{ color: green; }}
            </style>
        </head>
        <body>
            <div class="box">
                <h1>🧹 Очистка кэша</h1>
                <p class="success">✅ Удалено {result.get('deleted_count', 0)} старых файлов</p>
                <p>Освобождено {result.get('deleted_size_mb', 0):.2f} MB</p>
                <p><a href="{self._url('admin')}">← Назад в админку</a></p>
            </div>
        </body>
        </html>
        """
        
        headers = [('Content-Type', 'text/html; charset=utf-8')]
        start_response('200 OK', headers)
        return [html.encode('utf-8')]

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