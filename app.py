#!/usr/bin/env python3
"""
WSGI приложение для iCal синхронизации
С учетом префикса /8j0rn/
"""
import os
import sys
import base64
import json
import logging
import logging.handlers
import MySQLdb
import requests
from pathlib import Path
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import unquote

# ============================================================================
# ОТЛАДКА - добавляем в самое начало
# ============================================================================
print(f"\n{'='*60}", file=sys.stderr)
print(f"[APP DEBUG] Загрузка app.py начата", file=sys.stderr)
print(f"[APP DEBUG] Python: {sys.version}", file=sys.stderr)
print(f"[APP DEBUG] Путь: {__file__}", file=sys.stderr)
print(f"[APP DEBUG] Текущая директория: {os.getcwd()}", file=sys.stderr)

# Загружаем переменные окружения из .env
from dotenv import load_dotenv

# Определяем путь к .env файлу
BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / '.env'

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
    print(f"✓ Загружена конфигурация из {ENV_PATH}")
else:
    print(f"⚠ Внимание: файл {ENV_PATH} не найден. Используются переменные окружения системы.")

try:
    from filename_utils import FilenameManager
    print("✓ FilenameManager загружен")
except ImportError as e:
    print(f"⚠ Ошибка импорта FilenameManager: {e}")
    # Создаем простую заглушку
    class FilenameManager:
        @staticmethod
        def get_ics_filename(ical_key, property_id=None):
            if ical_key:
                # Простая очистка
                import re
                safe = re.sub(r'[^\w\-]', '_', str(ical_key))[:100]
                return f"{safe}.ics"
            elif property_id:
                return f"{property_id}.ics"
            return None
        
        @staticmethod
        def get_err_filename(ical_key, property_id=None):
            if ical_key:
                import re
                safe = re.sub(r'[^\w\-]', '_', str(ical_key))[:100]
                return f"{safe}.err"
            elif property_id:
                return f"{property_id}.err"
            return "unknown.err"

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
    WP_URL = os.environ.get('WP_URL', 'https://коттеджиказани.рф').rstrip('/')
    ICAL_ENDPOINT = os.environ.get('ICAL_ENDPOINT', '/ical-feed?ical=')
    
    # Аутентификация
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')
    
    # Кэширование
    CACHE_MAX_AGE = int(os.environ.get('CACHE_MAX_AGE', '3600'))  # 1 час
    CACHE_CLEANUP_DAYS = int(os.environ.get('CACHE_CLEANUP_DAYS', '7'))
    CACHE_MAX_SIZE_MB = int(os.environ.get('CACHE_MAX_SIZE_MB', '100'))
    
    # Публичные пути (без авторизации)
    PUBLIC_PATHS = [p.strip() for p in os.environ.get('PUBLIC_PATHS', '/ical/,/health/,/public/').split(',') if p.strip()]
    
    # Логирование
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    LOG_FILE = os.environ.get('LOG_FILE', 'data/logs/app.log')
    LOG_MAX_SIZE = int(os.environ.get('LOG_MAX_SIZE', '10'))  # MB
    LOG_BACKUP_COUNT = int(os.environ.get('LOG_BACKUP_COUNT', '5'))
    
    # Синхронизация
    SYNC_BATCH_SIZE = int(os.environ.get('SYNC_BATCH_SIZE', '10'))
    SYNC_DELAY = float(os.environ.get('SYNC_DELAY', '0.5'))
    SYNC_TIMEOUT = int(os.environ.get('SYNC_TIMEOUT', '30'))
    
    # Веб-сервер
    HOST = os.environ.get('HOST', '0.0.0.0')
    PORT = int(os.environ.get('PORT', '8000'))
    DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'
    
    # Пути
    BASE_DIR = BASE_DIR
    DATA_DIR = BASE_DIR / 'data'
    CACHE_DIR = DATA_DIR / 'cache'
    LOGS_DIR = DATA_DIR / 'logs'
    PUBLIC_DIR = BASE_DIR / 'public'
    PUBLIC_ICAL_DIR = PUBLIC_DIR / 'ical'
    
    # БАЗОВЫЙ ПУТЬ ПРИЛОЖЕНИЯ - ВАЖНО!
    # Если приложение доступно по https://домен.рф/8j0rn/
    # то BASE_PATH должен быть '/8j0rn'
    BASE_PATH = os.environ.get('BASE_PATH', '/8j0rn')
    
    @classmethod
    def setup_dirs(cls):
        """Создание необходимых директорий"""
        dirs = [
            cls.CACHE_DIR,
            cls.LOGS_DIR,
            cls.PUBLIC_ICAL_DIR
        ]
        
        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=True)
            print(f"✓ Проверена директория: {directory}")
    
    @classmethod
    def validate(cls):
        """Проверка конфигурации"""
        errors = []
        
        if not cls.DB_PASS:
            errors.append("DB_PASS не установлен")
        
        if not cls.WP_URL.startswith(('http://', 'https://')):
            errors.append(f"WP_URL должен начинаться с http:// или https://, получено: {cls.WP_URL}")
        
        return errors

# Инициализация конфигурации
config = Config()
config.setup_dirs()

# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================
def setup_logging():
    """Настройка системы логирования"""
    # Создаем логгер
    logger = logging.getLogger('ical_sync')
    logger.setLevel(getattr(logging, config.LOG_LEVEL))
    
    # Формат сообщений
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Файловый обработчик с ротацией
    log_file = config.BASE_DIR / config.LOG_FILE
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=config.LOG_MAX_SIZE * 1024 * 1024,  # MB to bytes
        backupCount=config.LOG_BACKUP_COUNT
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Консольный обработчик (только в debug режиме)
    if config.DEBUG:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # Отключаем логирование для некоторых библиотек
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    
    return logger

logger = setup_logging()

# ============================================================================
# БАЗА ДАННЫХ
# ============================================================================
class Database:
    """Управление подключением к БД WordPress"""
    
    @staticmethod
    def get_connection():
        """Создание подключения к БД"""
        try:
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
        except MySQLdb.Error as e:
            logger.error(f"Ошибка подключения к БД: {e}")
            raise
    
    @staticmethod
    @lru_cache(maxsize=128)
    def get_ical_key(property_id):
        """Получение iCal ключа для объекта (с кэшированием)"""
        conn = None
        cursor = None
        
        try:
            conn = Database.get_connection()
            cursor = conn.cursor()
            
            query = """
                SELECT meta_value 
                FROM wp_postmeta 
                WHERE post_id = %s 
                  AND meta_key = 'unique_code_ica'
                LIMIT 1
            """
            
            cursor.execute(query, (property_id,))
            result = cursor.fetchone()
            
            if result:
                key = result[0]
                logger.debug(f"Найден ключ для {property_id}: {key[:10]}...")
                return key
            else:
                logger.warning(f"Ключ не найден для объекта {property_id}")
                return None
                
        except MySQLdb.Error as e:
            logger.error(f"Ошибка БД при получении ключа для {property_id}: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    @staticmethod
    @lru_cache(maxsize=128)
    def get_id_by_key(ical_key):
        """Получение ID объекта по iCal ключу ТОЛЬКО для опубликованных объектов"""
        try:
            conn = Database.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT pm.post_id 
                FROM wp_postmeta pm
                INNER JOIN wp_posts p ON pm.post_id = p.ID
                WHERE pm.meta_key = 'unique_code_ica'
                  AND pm.meta_value = %s
                  AND p.post_type = 'estate_property'
                  AND p.post_status = 'publish'
                LIMIT 1
            """, (ical_key,))
            
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            return result[0] if result else None
            
        except MySQLdb.Error as e:
            logger.error(f"Ошибка БД при поиске ID по ключу {ical_key}: {e}")
            return None

    @staticmethod
    def get_all_keys():
        """Получение всех iCal ключей ТОЛЬКО для опубликованных объектов недвижимости"""
        try:
            conn = Database.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT pm.meta_value, pm.post_id
                FROM wp_postmeta pm
                INNER JOIN wp_posts p ON pm.post_id = p.ID
                WHERE pm.meta_key = 'unique_code_ica'
                  AND pm.meta_value IS NOT NULL
                  AND pm.meta_value != ''
                  AND LENGTH(pm.meta_value) > 10
                  AND p.post_type = 'estate_property'
                  AND p.post_status = 'publish'
                ORDER BY pm.post_id
            """)
            
            results = cursor.fetchall()
            cursor.close()
            conn.close()
            
            logger.info(f"Найдено ключей для опубликованных объектов: {len(results)}")
            return results  # Возвращаем (ключ, ID)
            
        except MySQLdb.Error as e:
            logger.error(f"Ошибка получения ключей: {e}")
            return []

    @staticmethod
    def get_key_by_id(property_id):
        """Получение iCal ключа по ID объекта ТОЛЬКО если объект опубликован"""
        try:
            conn = Database.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT pm.meta_value 
                FROM wp_postmeta pm
                INNER JOIN wp_posts p ON pm.post_id = p.ID
                WHERE pm.post_id = %s 
                  AND pm.meta_key = 'unique_code_ica'
                  AND p.post_type = 'estate_property'
                  AND p.post_status = 'publish'
                LIMIT 1
            """, (property_id,))
            
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            return result[0] if result else None
            
        except MySQLdb.Error as e:
            logger.error(f"Ошибка БД для объекта {property_id}: {e}")
            return None

    @staticmethod
    def get_all_properties():
        """Получение всех ОПУБЛИКОВАННЫХ объектов с iCal ключами"""
        conn = None
        cursor = None
        
        try:
            conn = Database.get_connection()
            cursor = conn.cursor()
            
            query = """
                SELECT pm.post_id, pm.meta_value 
                FROM wp_postmeta pm
                INNER JOIN wp_posts p ON pm.post_id = p.ID
                WHERE pm.meta_key = 'unique_code_ica'
                  AND pm.meta_value != ''
                  AND p.post_type = 'estate_property'
                  AND p.post_status = 'publish'
            """
            
            cursor.execute(query)
            results = cursor.fetchall()
            
            logger.info(f"Получено {len(results)} ОПУБЛИКОВАННЫХ объектов из БД")
            return results
            
        except MySQLdb.Error as e:
            logger.error(f"Ошибка БД при получении объектов: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @staticmethod
    def is_property_published(property_id):
        """Проверяет, опубликован ли объект недвижимости"""
        try:
            conn = Database.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT ID 
                FROM wp_posts 
                WHERE ID = %s 
                  AND post_type = 'estate_property'
                  AND post_status = 'publish'
                LIMIT 1
            """, (property_id,))
            
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            return bool(result)
            
        except MySQLdb.Error as e:
            logger.error(f"Ошибка проверки статуса объекта {property_id}: {e}")
            return False
    
    @staticmethod
    def get_properties_without_ical():
        """Получение всех опубликованных объектов НЕ имеющих iCal ключ"""
        conn = None
        cursor = None
        
        try:
            conn = Database.get_connection()
            cursor = conn.cursor()
            
            # Находим объекты, у которых нет ключа unique_code_ica
            query = """
                SELECT p.ID, p.post_title, p.post_date
                FROM wp_posts p
                LEFT JOIN wp_postmeta pm ON p.ID = pm.post_id AND pm.meta_key = 'unique_code_ica'
                WHERE p.post_type = 'estate_property'
                  AND p.post_status = 'publish'
                  AND pm.meta_id IS NULL  -- Это означает, что ключ не найден
                ORDER BY p.ID
            """
            
            cursor.execute(query)
            results = cursor.fetchall()
            
            logger.info(f"Найдено {len(results)} объектов БЕЗ iCal ключа")
            return results
            
        except MySQLdb.Error as e:
            logger.error(f"Ошибка БД при получении объектов без iCal: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

# ============================================================================
# МЕНЕДЖЕР iCal
# ============================================================================
class ICalManager:
    """Исправленный менеджер iCal файлов"""
    
    @staticmethod
    def get_cache_path(ical_key, property_id=None):
        """ЕДИНЫЙ метод получения пути к кэшу"""
        # Импортируем config здесь чтобы избежать циклических импортов
        from app import config
        
        if not ical_key and not property_id:
            return None
        
        # Используем FilenameManager
        filename = FilenameManager.get_ics_filename(ical_key, property_id)
        if not filename:
            return None
            
        cache_dir = Path(config.CACHE_DIR)
        return cache_dir / filename
    
    @staticmethod
    def get_error_path(ical_key, property_id=None):
        """Путь для файла ошибок"""
        from app import config
        
        if not ical_key and not property_id:
            return None
        
        filename = FilenameManager.get_err_filename(ical_key, property_id)
        if not filename:
            return None
            
        cache_dir = Path(config.CACHE_DIR)
        return cache_dir / filename
    
    @staticmethod
    def is_cache_valid(ical_key, property_id=None):
        """Проверка актуальности кэша"""
        cache_file = ICalManager.get_cache_path(ical_key, property_id)
        
        if not cache_file or not cache_file.exists():
            return False
        
        from datetime import datetime
        from app import config
        
        file_age = datetime.now().timestamp() - cache_file.stat().st_mtime
        return file_age < config.CACHE_MAX_AGE
    
    @staticmethod
    def save_to_cache(ical_key, content, property_id=None):
        """Сохранение в кэш"""
        import os
        from pathlib import Path
        
        try:
            cache_file = ICalManager.get_cache_path(ical_key, property_id)
            
            if not cache_file:
                logger.error(f"Не могу определить путь для сохранения")
                return False
            
            # Создаем директорию если нет
            cache_file.parent.mkdir(exist_ok=True, parents=True)
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                f.write(content)
            
            os.chmod(cache_file, 0o644)
            logger.info(f"✓ Сохранен: {cache_file.name} ({len(content)} байт)")
            
            # Логируем что сохранили
            print(f"DEBUG: Файл сохранен как: {cache_file.name}")
            print(f"DEBUG: Ключ: {ical_key}, ID: {property_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"✗ Ошибка сохранения: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    @staticmethod
    def save_error_response(ical_key, response, property_id=None):
        """Сохранение ошибки для отладки"""
        try:
            err_file = ICalManager.get_error_path(ical_key, property_id)
            
            if not err_file:
                return False
            
            with open(err_file, 'w', encoding='utf-8') as f:
                f.write(f"URL: {response.url if hasattr(response, 'url') else 'N/A'}\n")
                f.write(f"Status Code: {response.status_code if hasattr(response, 'status_code') else 'N/A'}\n")
                f.write(f"Content-Length: {response.headers.get('Content-Length', 'N/A')}\n")
                f.write(f"Content-Type: {response.headers.get('Content-Type', 'N/A')}\n")
                f.write(f"Headers:\n")
                
                if hasattr(response, 'headers'):
                    for key, value in response.headers.items():
                        if key not in ['Content-Length', 'Content-Type']:  # Уже вывели
                            f.write(f"  {key}: {value}\n")
                
                f.write(f"\nContent (first 5000 chars):\n")
                
                # Проверяем, есть ли контент
                if hasattr(response, 'content') and response.content:
                    content_preview = response.text[:5000] if hasattr(response, 'text') else str(response.content[:5000])
                    if not content_preview.strip():
                        content_preview = "[ПУСТОЙ ОТВЕТ]"
                else:
                    content_preview = "[НЕТ КОНТЕНТА]"
                
                f.write(f"{content_preview}\n")
                
                # Добавляем информацию о запросе
                f.write(f"\n--- Дополнительная информация ---\n")
                f.write(f"Key: {ical_key}\n")
                f.write(f"Property ID: {property_id}\n")
                f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            
            logger.warning(f"Сохранена ошибка: {err_file.name}")
            return True
            
        except Exception as e:
            logger.error(f"Не удалось сохранить ошибку: {e}")
            return False

    @staticmethod
    def download_ical(ical_key, property_id=None):
        """Универсальный метод загрузки iCal"""
        try:
            from urllib.parse import quote
            encoded_key = quote(ical_key)
            
            url = f"{config.WP_URL}{config.ICAL_ENDPOINT}{encoded_key}"
            logger.info(f"Загрузка iCal: {ical_key[:10]}...")
            
            import requests
            response = requests.get(
                url, 
                timeout=config.SYNC_TIMEOUT,
                headers={
                    'User-Agent': 'iCal-Sync-Service/1.0',
                    'Accept': 'text/calendar'
                }
            )
            
            # Проверяем Content-Type
            content_type = response.headers.get('Content-Type', '').lower()
            
            # Проверяем, что ответ не пустой
            if len(response.content) == 0:
                logger.warning(f"Пустой ответ от сервера")
                ICalManager.save_error_response(ical_key, response, property_id)
                return None
            
            if 'text/calendar' not in content_type:
                logger.warning(f"Неверный Content-Type: {content_type}")
                
                # Сохраняем ошибку
                ICalManager.save_error_response(ical_key, response, property_id)
                
                # Проверяем, это HTML ошибка?
                if 'text/html' in content_type and len(response.text) < 1000:
                    logger.error(f"Получена HTML страница вместо iCal")
                    return None
            
            response.raise_for_status()
            return response.text
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка загрузки {ical_key}: {e}")
            return None

# ============================================================================
# WSGI ПРИЛОЖЕНИЕ С ПРАВИЛЬНЫМИ ССЫЛКАМИ
# ============================================================================
class ICalApp:
    """Основное WSGI приложение с учетом BASE_PATH"""
    
    def __init__(self):
        print(f"[APP DEBUG] Инициализация ICalApp", file=sys.stderr)
        self.db = Database()
        self.ical = ICalManager()
        
        # Валидация конфигурации при запуске
        errors = config.validate()
        if errors:
            print(f"[APP DEBUG] Проблемы с конфигурацией: {errors}", file=sys.stderr)
    
    def _url(self, path):
        """Формирует URL с учетом BASE_PATH"""
        # Убираем начальный слеш если он есть в path
        if path.startswith('/'):
            path = path[1:]
        
        # Добавляем BASE_PATH
        return f"{config.BASE_PATH}/{path}" if path else config.BASE_PATH
    
    def is_public_path(self, path):
        """Проверяет, является ли путь публичным"""
        # Убираем BASE_PATH из path для проверки
        if path.startswith(config.BASE_PATH):
            path = path[len(config.BASE_PATH):]
        
        for public_path in config.PUBLIC_PATHS:
            if path.startswith(public_path):
                return True
        return False
    
    def check_auth(self, environ):
        """Проверка авторизации HTTP Basic Auth"""
        auth_header = environ.get('HTTP_AUTHORIZATION', '')
        
        if not auth_header.startswith('Basic '):
            return False
        
        try:
            # Декодируем логин и пароль
            auth_decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
            username, password = auth_decoded.split(':', 1)
            
            return username == config.ADMIN_USERNAME and password == config.ADMIN_PASSWORD
            
        except Exception as e:
            logger.error(f"Ошибка проверки авторизации: {e}")
            return False
    
    def require_auth(self, environ, start_response):
        """Запрос авторизации"""
        headers = [
            ('WWW-Authenticate', 'Basic realm="iCal Admin Area"'),
            ('Content-Type', 'text/html; charset=utf-8')
        ]
        start_response('401 Unauthorized', headers)
        
        base_path = config.BASE_PATH
        ical_url = self._url('ical/1.ics')
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Требуется авторизация</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                h1 {{ color: #333; }}
                .box {{ 
                    max-width: 500px; 
                    margin: 0 auto; 
                    padding: 30px; 
                    border: 1px solid #ddd; 
                    border-radius: 10px; 
                    background: #f9f9f9;
                }}
            </style>
        </head>
        <body>
            <div class="box">
                <h1>Требуется авторизация</h1>
                <p>Введите логин и пароль администратора</p>
                <p><small>Это защищенная область. iCal файлы доступны по <a href="{ical_url}">ссылкам</a> без пароля.</small></p>
            </div>
        </body>
        </html>
        """
        
        return [html.encode('utf-8')]
    
    def __call__(self, environ, start_response):
        """Основной обработчик WSGI"""
        # ОТЛАДКА - выводим все переменные
        print(f"\n[APP DEBUG] {'='*50}", file=sys.stderr)
        print(f"[APP DEBUG] Новый запрос", file=sys.stderr)
        print(f"[APP DEBUG] PATH_INFO: '{environ.get('PATH_INFO', '')}'", file=sys.stderr)
        print(f"[APP DEBUG] SCRIPT_NAME: '{environ.get('SCRIPT_NAME', '')}'", file=sys.stderr)
        print(f"[APP DEBUG] REQUEST_METHOD: {environ.get('REQUEST_METHOD', '')}", file=sys.stderr)
        print(f"[APP DEBUG] QUERY_STRING: {environ.get('QUERY_STRING', '')}", file=sys.stderr)
        
        # ВРЕМЕННО отключаем авторизацию для теста
        path = environ.get('PATH_INFO', '/')
        
        if not path:
            path = '/'
        elif not path.startswith('/'):
            path = '/' + path
        
        print(f"[APP DEBUG] Обрабатываем путь: '{path}'", file=sys.stderr)
        
        try:
            # Пробуем обработать запрос
            result = self.route_request(path, environ, start_response)
            print(f"[APP DEBUG] Запрос обработан успешно", file=sys.stderr)
            return result
        except Exception as e:
            print(f"[APP DEBUG] Ошибка обработки: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            
            # Возвращаем ошибку
            headers = [('Content-Type', 'text/plain; charset=utf-8')]
            start_response('500 Internal Server Error', headers)
            return [f"Ошибка приложения: {str(e)}".encode('utf-8')]
    
    def route_request(self, path, environ, start_response):
        """Маршрутизация запросов"""
        print(f"[APP DEBUG] route_request: path='{path}'", file=sys.stderr)
        
        method = environ.get('REQUEST_METHOD', 'GET')
        print(f"[APP DEBUG] method='{method}'", file=sys.stderr)
        
        # Убираем BASE_PATH если он есть
        if config.BASE_PATH and path.startswith(config.BASE_PATH):
            path = path[len(config.BASE_PATH):]
            print(f"[APP DEBUG] Убрали BASE_PATH, новый путь='{path}'", file=sys.stderr)
        
        # Нормализуем путь
        if not path:
            path = '/'
        elif not path.startswith('/'):
            path = '/' + path
        
        print(f"[APP DEBUG] Нормализованный путь='{path}'", file=sys.stderr)
        
        # Обрабатываем пути
        if method == 'GET' and path.startswith('/ical/'):
            print(f"[APP DEBUG] → handle_ical_request", file=sys.stderr)
            return self.handle_ical_request(path, environ, start_response)
        elif method == 'GET' and path == '/sync':
            print(f"[APP DEBUG] → handle_sync_request", file=sys.stderr)
            return self.handle_sync_request(environ, start_response)
        elif method == 'GET' and path.startswith('/sync/'):
            print(f"[APP DEBUG] → handle_sync_single", file=sys.stderr)
            return self.handle_sync_single(path, environ, start_response)
        elif method == 'GET' and path == '/admin':
            print(f"[APP DEBUG] → handle_admin", file=sys.stderr)
            return self.handle_admin(environ, start_response)
        elif method == 'GET' and path == '/health':
            print(f"[APP DEBUG] → handle_health", file=sys.stderr)
            return self.handle_health(environ, start_response)
        elif method == 'GET' and (path == '/' or path == '/index.wsgi'):
            print(f"[APP DEBUG] → handle_root", file=sys.stderr)
            return self.handle_root(environ, start_response)
        elif method == 'GET' and path == '/stats':
            print(f"[APP DEBUG] → handle_stats", file=sys.stderr)
            return self.handle_stats(environ, start_response)
        else:
            print(f"[APP DEBUG] → handle_404 (путь не найден)", file=sys.stderr)
            return self.handle_404(environ, start_response, f"Path not found: {path}")

    # Добавляем новый метод в ICalManager
    def fetch_ical_by_key(self, ical_key):
        """Загрузка iCal по ключу"""
        try:
            from urllib.parse import quote
            encoded_key = quote(ical_key)
            
            url = f"{config.WP_URL}{config.ICAL_ENDPOINT}{encoded_key}"
            logger.info(f"Загрузка iCal для ключа {ical_key[:10]}...")
            
            response = requests.get(
                url, 
                timeout=config.SYNC_TIMEOUT,
                headers={
                    'User-Agent': 'iCal-Sync-Service/1.0',
                    'Accept': 'text/calendar'
                }
            )
            response.raise_for_status()
            
            return response.text
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка загрузки iCal для ключа {ical_key}: {e}")
            return None

    def handle_ical_request(self, path, environ, start_response):
        """Обработка запроса iCal файла по ключу - ЧИТАЕМ ИЗ КЭША"""
        try:
            # Извлекаем имя файла из пути
            filename = path.split('/')[-1]
            
            if not filename or not filename.endswith('.ics'):
                return self.handle_404(environ, start_response, "Неверный формат файла")
            
            # Путь к файлу в кэше
            cache_file = config.CACHE_DIR / filename
            
            if not cache_file.exists():
                logger.warning(f"Файл не найден в кэше: {filename}")
                
                # Пробуем найти по ID
                file_id = filename[:-4]  # Убираем .ics
                try:
                    property_id = int(file_id)
                    # Пробуем найти файл по ID
                    for f in config.CACHE_DIR.glob("*.ics"):
                        if f.stem == str(property_id):
                            cache_file = f
                            break
                except ValueError:
                    pass
                
                if not cache_file.exists():
                    return self.handle_404(environ, start_response, 
                                        f"iCal файл {filename} не найден")
            
            # Читаем содержимое файла
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                if not content:
                    raise ValueError("Файл пустой")
                    
            except Exception as e:
                logger.error(f"Ошибка чтения файла {cache_file}: {e}")
                return self.handle_error(f"Ошибка чтения iCal файла", 500, environ, start_response)
            
            # Успешный ответ
            headers = [
                ('Content-Type', 'text/calendar; charset=utf-8'),
                ('Content-Disposition', f'inline; filename="{filename}"'),
                ('Cache-Control', 'public, max-age=300'),
                ('Content-Length', str(len(content.encode('utf-8')))),
                ('X-Served-By', 'python-wsgi'),
                ('X-File-Size', str(len(content)))
            ]
            
            start_response('200 OK', headers)
            return [content.encode('utf-8')]
            
        except Exception as e:
            logger.error(f"Ошибка в handle_ical_request: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self.handle_error(f"Internal Server Error: {str(e)}", 500, environ, start_response)
    
    def handle_sync_request(self, environ, start_response):
      """Запуск синхронизации всех объектов"""
      try:
          from ical_logic import sync_all_properties
          result = sync_all_properties()
          
          headers = [('Content-Type', 'application/json; charset=utf-8')]
          start_response('200 OK', headers)
          
          response = {
              'status': 'success',
              'message': 'Синхронизация завершена',
              'result': result,
              'timestamp': datetime.now().isoformat()
          }
          
          return [json.dumps(response, ensure_ascii=False, indent=2).encode('utf-8')]
          
      except Exception as e:
          logger.error(f"Ошибка синхронизации: {e}")
          return self.handle_error(f"Sync error: {str(e)}", 500, environ, start_response)
    
    def handle_admin(self, environ, start_response):
        """Админ-панель (требует авторизации)"""
        # Получаем статистику
        cache_files = list(config.CACHE_DIR.glob("*.ics"))
        total_size = sum(f.stat().st_size for f in cache_files) / (1024 * 1024)  # MB
        
        # Самые свежие файлы
        recent_files = sorted(cache_files, key=lambda x: x.stat().st_mtime, reverse=True)[:10]
        
        # Формируем URL с учетом BASE_PATH
        base_path = config.BASE_PATH
        sync_url = self._url('sync')
        home_url = self._url('index.wsgi')
        ical_example_url = self._url('ical/1.ics')
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>iCal Sync Admin</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * {{ box-sizing: border-box; }}
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                    line-height: 1.6; 
                    margin: 0; 
                    padding: 20px; 
                    background: #f5f5f5;
                    color: #333;
                }}
                .container {{ 
                    max-width: 1200px; 
                    margin: 0 auto; 
                }}
                header {{ 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 2rem;
                    border-radius: 10px;
                    margin-bottom: 2rem;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }}
                h1 {{ margin: 0 0 0.5rem 0; font-size: 2.5rem; }}
                .subtitle {{ opacity: 0.9; font-size: 1.1rem; }}
                .stats-grid {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                    gap: 1rem;
                    margin-bottom: 2rem;
                }}
                .stat-card {{
                    background: white;
                    padding: 1.5rem;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                }}
                .stat-number {{
                    font-size: 2rem;
                    font-weight: bold;
                    color: #667eea;
                    margin-bottom: 0.5rem;
                }}
                .stat-label {{ color: #666; font-size: 0.9rem; }}
                .actions {{
                    display: flex;
                    gap: 1rem;
                    flex-wrap: wrap;
                    margin-bottom: 2rem;
                }}
                .btn {{
                    display: inline-block;
                    padding: 0.8rem 1.5rem;
                    background: #667eea;
                    color: white;
                    text-decoration: none;
                    border-radius: 6px;
                    border: none;
                    cursor: pointer;
                    font-size: 1rem;
                    transition: all 0.2s;
                }}
                .btn:hover {{
                    background: #5a67d8;
                    transform: translateY(-2px);
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }}
                .btn-danger {{ background: #e53e3e; }}
                .btn-danger:hover {{ background: #c53030; }}
                .btn-success {{ background: #38a169; }}
                .btn-success:hover {{ background: #2f855a; }}
                table {{
                    width: 100%;
                    background: white;
                    border-radius: 8px;
                    overflow: hidden;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                    margin-bottom: 2rem;
                }}
                th, td {{
                    padding: 1rem;
                    text-align: left;
                    border-bottom: 1px solid #e2e8f0;
                }}
                th {{ background: #f7fafc; font-weight: 600; color: #4a5568; }}
                tr:hover {{ background: #f7fafc; }}
                .badge {{
                    display: inline-block;
                    padding: 0.25rem 0.5rem;
                    border-radius: 12px;
                    font-size: 0.75rem;
                    font-weight: 600;
                }}
                .badge-success {{ background: #c6f6d5; color: #22543d; }}
                .badge-warning {{ background: #feebc8; color: #744210; }}
                .badge-error {{ background: #fed7d7; color: #742a2a; }}
                footer {{
                    text-align: center;
                    margin-top: 3rem;
                    color: #718096;
                    font-size: 0.9rem;
                }}
                @media (max-width: 768px) {{
                    .actions {{ flex-direction: column; }}
                    .btn {{ width: 100%; text-align: center; }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <header>
                    <h1>iCal Sync Admin</h1>
                    <p class="subtitle">Управление синхронизацией календарей для Авито</p>
                </header>
                
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-number">{len(cache_files)}</div>
                        <div class="stat-label">Файлов в кэше</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{total_size:.1f} MB</div>
                        <div class="stat-label">Размер кэша</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{len(recent_files)}</div>
                        <div class="stat-label">Активных файлов</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{config.CACHE_MAX_AGE // 60} мин</div>
                        <div class="stat-label">Время жизни кэша</div>
                    </div>
                </div>
                
                <div class="actions">
                    <a href="{sync_url}" class="btn btn-success">🔄 Синхронизировать все</a>
                    <a href="{home_url}" class="btn">🏠 На главную</a>
                    <a href="{ical_example_url}" target="_blank" class="btn">📅 Пример iCal</a>
                    <button onclick="location.reload()" class="btn">🔄 Обновить</button>
                </div>
                
                <h2>Последние файлы</h2>
                <table>
                    <thead>
                        <tr>
                            <th>ID объекта</th>
                            <th>Размер</th>
                            <th>Дата изменения</th>
                            <th>Ссылка</th>
                            <th>Статус</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join(self._generate_file_rows(recent_files))}
                    </tbody>
                </table>
                
                <footer>
                    <p>iCal Sync Service v1.0 • {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
                    <p>Для Авито: {config.WP_URL}{base_path}/ical/[ID].ics</p>
                </footer>
            </div>
            
            <script>
                // Автоматическое обновление каждые 30 секунд
                setTimeout(() => location.reload(), 30000);
            </script>
        </body>
        </html>
        """
        
        headers = [('Content-Type', 'text/html; charset=utf-8')]
        start_response('200 OK', headers)
        return [html.encode('utf-8')]
    
    def _generate_file_rows(self, cache_files):
        """Генерация строк таблицы с файлами"""
        rows = []
        base_path = config.BASE_PATH
        
        for file_path in cache_files:
            file_id = file_path.stem
            size_kb = file_path.stat().st_size / 1024
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            age = datetime.now() - mtime
            
            # Определяем статус
            if age.total_seconds() < config.CACHE_MAX_AGE:
                status_badge = '<span class="badge badge-success">Актуальный</span>'
            elif age.total_seconds() < config.CACHE_MAX_AGE * 2:
                status_badge = '<span class="badge badge-warning">Устаревает</span>'
            else:
                status_badge = '<span class="badge badge-error">Устарел</span>'
            
            # Формируем ссылку с учетом BASE_PATH
            ical_url = self._url(f'ical/{file_id}.ics')
            
            rows.append(f"""
                <tr>
                    <td><strong>{file_id}</strong></td>
                    <td>{size_kb:.1f} KB</td>
                    <td>{mtime.strftime('%Y-%m-%d %H:%M')}<br><small>({self._format_timedelta(age)})</small></td>
                    <td><a href="{ical_url}" target="_blank">📥 Скачать</a></td>
                    <td>{status_badge}</td>
                </tr>
            """)
        return rows
    
    def _format_timedelta(self, td):
        """Форматирование timedelta в читаемый вид"""
        if td.days > 0:
            return f"{td.days} дн. назад"
        elif td.seconds > 3600:
            return f"{td.seconds // 3600} ч. назад"
        elif td.seconds > 60:
            return f"{td.seconds // 60} мин. назад"
        else:
            return f"{td.seconds} сек. назад"
    
    def handle_health(self, environ, start_response):
        """Health check endpoint (публичный)"""
        health_status = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'service': 'iCal Sync Service',
            'version': '1.0',
            'cache_files': len(list(config.CACHE_DIR.glob("*.ics"))),
            'base_path': config.BASE_PATH,
            'uptime': 'unknown'
        }
        
        headers = [('Content-Type', 'application/json; charset=utf-8')]
        start_response('200 OK', headers)
        return [json.dumps(health_status, ensure_ascii=False, indent=2).encode('utf-8')]
    
    def handle_root(self, environ, start_response):
        """Главная страница (требует авторизации)"""
        base_path = config.BASE_PATH
        admin_url = self._url('admin')
        sync_url = self._url('sync')
        health_url = self._url('health')
        ical_example_url = self._url('ical/1.ics')
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>iCal Sync Service</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                    max-width: 800px; 
                    margin: 0 auto; 
                    padding: 2rem; 
                    line-height: 1.6;
                    background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                    min-height: 100vh;
                }}
                .card {{
                    background: white;
                    padding: 2rem;
                    border-radius: 15px;
                    box-shadow: 0 10px 30px rgba(0,0,0,0.1);
                    margin-bottom: 2rem;
                }}
                h1 {{ 
                    color: #2d3748; 
                    margin-top: 0;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                }}
                .endpoints {{
                    display: grid;
                    gap: 1rem;
                    margin: 2rem 0;
                }}
                .endpoint {{
                    background: #f7fafc;
                    padding: 1.5rem;
                    border-radius: 10px;
                    border-left: 4px solid #667eea;
                }}
                code {{
                    background: #edf2f7;
                    padding: 0.2rem 0.5rem;
                    border-radius: 4px;
                    font-family: 'SF Mono', Monaco, monospace;
                    font-size: 0.9em;
                }}
                a {{
                    color: #667eea;
                    text-decoration: none;
                    font-weight: 500;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
                .btn {{
                    display: inline-block;
                    background: #667eea;
                    color: white;
                    padding: 0.8rem 1.5rem;
                    border-radius: 6px;
                    text-decoration: none;
                    margin-top: 1rem;
                    transition: transform 0.2s;
                }}
                .btn:hover {{
                    transform: translateY(-2px);
                    text-decoration: none;
                }}
                .info {{
                    background: #e6fffa;
                    border-left: 4px solid #38b2ac;
                    padding: 1rem;
                    border-radius: 4px;
                    margin: 1rem 0;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>iCal Sync Service</h1>
                <p>Сервис синхронизации iCal файлов для WordPress и Авито</p>
                
                <div class="info">
                    <strong>Для Авито используйте ссылки:</strong><br>
                    <code>{config.WP_URL}{base_path}/ical/[ID_объекта].ics</code>
                </div>
                
                <div class="endpoints">
                    <div class="endpoint">
                        <h3>📅 Получить iCal файл</h3>
                        <p><strong>URL:</strong> <code>GET {base_path}/ical/{{id}}.ics</code></p>
                        <p><strong>Доступ:</strong> Публичный (без пароля)</p>
                        <p><strong>Пример:</strong> <a href="{ical_example_url}" target="_blank">{base_path}/ical/1.ics</a></p>
                    </div>
                    
                    <div class="endpoint">
                        <h3>🔄 Синхронизация</h3>
                        <p><strong>URL:</strong> <code>GET {base_path}/sync</code></p>
                        <p><strong>Доступ:</strong> Только для администраторов</p>
                        <p>Обновляет все iCal файлы из WordPress</p>
                    </div>
                    
                    <div class="endpoint">
                        <h3>📊 Админ-панель</h3>
                        <p><strong>URL:</strong> <code>GET {base_path}/admin</code></p>
                        <p><strong>Доступ:</strong> Только для администраторов</p>
                        <p>Статистика и управление кэшем</p>
                    </div>
                    
                    <div class="endpoint">
                        <h3>❤️ Health check</h3>
                        <p><strong>URL:</strong> <code>GET {base_path}/health</code></p>
                        <p><strong>Доступ:</strong> Публичный (без пароля)</p>
                        <p>Проверка работоспособности сервиса</p>
                    </div>
                </div>
                
                <a href="{admin_url}" class="btn">Перейти в админ-панель →</a>
            </div>
            
            <div class="card">
                <h2>📖 Документация</h2>
                <h3>Как это работает:</h3>
                <ol>
                    <li>Сервис загружает iCal файлы из WordPress по ключам</li>
                    <li>Сохраняет их в кэш на сервере</li>
                    <li>Предоставляет статические ссылки для Авито</li>
                    <li>Автоматически обновляет кэш каждые 2 часа</li>
                </ol>
                
                <h3>Настройка Авито:</h3>
                <p>Используйте ссылки вида: <code>{config.WP_URL}{base_path}/ical/123.ics</code></p>
                <p>Где <code>123</code> - ID объекта недвижимости в WordPress</p>
            </div>
        </body>
        </html>
        """
        
        headers = [('Content-Type', 'text/html; charset=utf-8')]
        start_response('200 OK', headers)
        return [html.encode('utf-8')]
    
    def handle_404(self, environ, start_response, message="Not Found"):
        """Обработка 404 ошибки"""
        return self.handle_error(message, 404, environ, start_response)
    
    def handle_error(self, message, status_code, environ, start_response):
        """Обработка ошибок"""
        error_messages = {
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            500: "Internal Server Error"
        }
        
        status_text = error_messages.get(status_code, "Error")
        
        if config.DEBUG:
            # В режиме отладки возвращаем JSON
            headers = [('Content-Type', 'application/json; charset=utf-8')]
            start_response(f'{status_code} {status_text}', headers)
            
            error_response = {
                'error': status_text,
                'message': message,
                'status_code': status_code,
                'path': environ.get('PATH_INFO', ''),
                'timestamp': datetime.now().isoformat()
            }
            
            return [json.dumps(error_response, ensure_ascii=False, indent=2).encode('utf-8')]
        else:
            # В production возвращаем простой текст
            headers = [('Content-Type', 'text/plain; charset=utf-8')]
            start_response(f'{status_code} {status_text}', headers)
            return [f"{status_text}: {message}".encode('utf-8')]

# ============================================================================
# Создание экземпляра приложения
# ============================================================================
application = ICalApp()

# ============================================================================
# Запуск для разработки
# ============================================================================
if __name__ == '__main__':
    # Валидация конфигурации
    errors = config.validate()
    if errors:
        print("\n" + "="*60)
        print("ОШИБКИ КОНФИГУРАЦИИ:")
        for error in errors:
            print(f"  • {error}")
        print("="*60)
        
        if not config.DEBUG:
            sys.exit(1)
    
    print(f"\n{'='*60}")
    print("🚀 iCal Sync Service")
    print(f"{'='*60}")
    print(f"📁 Директория:   {config.BASE_DIR}")
    print(f"🌐 URL WordPress: {config.WP_URL}")
    print(f"🗄️  База данных:   {config.DB_USER}@{config.DB_HOST}/{config.DB_NAME}")
    print(f"📊 Кэш:          {config.CACHE_DIR}")
    print(f"🔐 Админ:        {config.ADMIN_USERNAME} (пароль: {'*' * len(config.ADMIN_PASSWORD)})")
    print(f"📍 Базовый путь:  {config.BASE_PATH}")
    print(f"🌍 Публичные пути: {', '.join(config.PUBLIC_PATHS)}")
    print(f"{'='*60}")
    print(f"\nСервер запущен: http://{config.HOST}:{config.PORT}{config.BASE_PATH}")
    print(f"Для Авито:      http://{config.HOST}:{config.PORT}{config.BASE_PATH}/ical/[ID].ics")
    print(f"Админка:        http://{config.HOST}:{config.PORT}{config.BASE_PATH}/admin")
    print(f"Логи:           {config.LOG_FILE}")
    print(f"\nНажмите Ctrl+C для остановки")
    print("="*60 + "\n")
    
    try:
        from wsgiref.simple_server import make_server
        httpd = make_server(config.HOST, config.PORT, application)
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Сервер остановлен")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Ошибка запуска сервера: {e}")
        print(f"💥 Ошибка: {e}")
        sys.exit(1)