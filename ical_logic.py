#!/usr/bin/env python3
"""
ical_logic.py - Общая логика для работы с iCal
Используется и app.py и sync.py
"""
import time
from app import Database, ICalManager, config, logger

def sync_all_properties():
    """Основная функция синхронизации"""
    logger.info("Начало синхронизации")
    
    db = Database()
    ical = ICalManager()
    
    properties = db.get_all_properties()
    total = len(properties)
    
    logger.info(f"Найдено объектов: {total}")
    
    success = 0
    errors = 0
    skipped = 0
    
    for i, (property_id, ical_key) in enumerate(properties, 1):
        try:
            # Пропускаем если кэш актуален
            if ical.is_cache_valid(property_id):
                skipped += 1
                continue
            
            # Загружаем iCal
            content = ical.fetch_ical(property_id, ical_key)
            
            if content:
                # Сохраняем в кэш
                if ical.save_to_cache(property_id, content):
                    success += 1
                    logger.info(f"[{i}/{total}] Успешно: {property_id}")
                else:
                    errors += 1
                    logger.error(f"[{i}/{total}] Ошибка сохранения: {property_id}")
            else:
                errors += 1
                logger.error(f"[{i}/{total}] Не удалось загрузить: {property_id}")
                
        except Exception as e:
            errors += 1
            logger.error(f"[{i}/{total}] Критическая ошибка для {property_id}: {e}")
        
        # Задержка
        if i < total:
            time.sleep(config.SYNC_DELAY)
    
    result = {
        'total': total,
        'success': success,
        'errors': errors,
        'skipped': skipped,
        'timestamp': time.time()
    }
    
    logger.info(f"Синхронизация завершена: {result}")
    return result

def sync_single_property(property_id):
    """Синхронизация одного объекта"""
    logger.info(f"Синхронизация объекта {property_id}")
    
    db = Database()
    ical = ICalManager()
    
    ical_key = db.get_ical_key(property_id)
    
    if not ical_key:
        logger.error(f"Ключ не найден для объекта {property_id}")
        return {'status': 'error', 'message': 'Key not found'}
    
    content = ical.fetch_ical(property_id, ical_key)
    
    if content:
        if ical.save_to_cache(property_id, content):
            return {'status': 'success', 'message': 'Synchronized'}
        else:
            return {'status': 'error', 'message': 'Save failed'}
    else:
        return {'status': 'error', 'message': 'Fetch failed'}