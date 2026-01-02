#!/usr/bin/env python3
"""
filename_utils.py - Утилиты для работы с именами файлов
"""
import re
from pathlib import Path

class FilenameManager:
    """Управление именами файлов"""
    
    @staticmethod
    def sanitize_key(ical_key):
        """Очистка ключа для использования в имени файла"""
        if not ical_key:
            return None
        
        # Убираем все не-буквенно-цифровые символы, кроме дефиса и подчеркивания
        safe = re.sub(r'[^\w\-]', '_', str(ical_key))
        # Ограничиваем длину
        return safe[:100]
    
    @staticmethod
    def get_ics_filename(ical_key, property_id=None):
        """Получаем имя файла .ics"""
        if not ical_key:
            # Если ключа нет, используем ID (для обратной совместимости)
            if property_id:
                return f"{property_id}.ics"
            return None
        
        safe_key = FilenameManager.sanitize_key(ical_key)
        return f"{safe_key}.ics"
    
    @staticmethod
    def get_err_filename(ical_key, property_id=None):
        """Получаем имя файла .err для ошибок"""
        if not ical_key and property_id:
            return f"{property_id}.err"
        
        safe_key = FilenameManager.sanitize_key(ical_key) if ical_key else "unknown"
        return f"{safe_key}.err"
    
    @staticmethod
    def get_cache_path(config, ical_key, property_id=None, extension="ics"):
        """Полный путь к файлу в кэше"""
        from pathlib import Path
        
        if extension == "ics":
            filename = FilenameManager.get_ics_filename(ical_key, property_id)
        elif extension == "err":
            filename = FilenameManager.get_err_filename(ical_key, property_id)
        else:
            filename = f"{FilenameManager.sanitize_key(ical_key) if ical_key else property_id}.{extension}"
        
        if not filename:
            return None
            
        cache_dir = Path(config.CACHE_DIR) if hasattr(config, 'CACHE_DIR') else Path('data/cache')
        return cache_dir / filename
    
    @staticmethod
    def parse_filename(filename):
        """Парсит имя файла, извлекает ключ или ID"""
        if isinstance(filename, Path):
            name = filename.stem
        else:
            name = Path(filename).stem
        
        # Пробуем понять, это ключ или ID
        if isinstance(name, str) and name.isdigit():
            return {"type": "id", "value": int(name)}
        elif isinstance(name, str) and len(name) == 32 and all(c in '0123456789abcdef' for c in name.lower()):
            return {"type": "key", "value": name}
        else:
            return {"type": "unknown", "value": name}