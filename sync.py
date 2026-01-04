#!/usr/bin/env python3
"""
sync.py - Рабочая версия синхронизации
"""
import os
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from app import config, Database, ICalManager

def sync_keys(limit=None):
    """Синхронизация ключей ТОЛЬКО для опубликованных объектов"""
    db = Database()
    ical = ICalManager()
    
    # Получаем ключи ТОЛЬКО опубликованных объектов
    all_keys = db.get_all_keys()
    
    if not all_keys:
        print("❌ Нет ключей в базе для опубликованных объектов!")
        return {"total": 0, "success": 0, "errors": 0, "skipped": 0}
    
    # Ограничиваем если нужно
    if limit and limit > 0:
        keys_to_process = all_keys[:limit]
        print(f"🧪 ТЕСТОВЫЙ РЕЖИМ: {limit} ключей из {len(all_keys)} опубликованных объектов")
    else:
        keys_to_process = all_keys
        print(f"🔄 ПОЛНАЯ СИНХРОНИЗАЦИЯ: {len(all_keys)} ключей (только опубликованные объекты)")
    
    print("="*60)
    print("Тип записи: estate_property, Статус: publish")
    print("="*60)
    
    success = 0
    errors = 0
    skipped = 0
    
    for i, (ical_key, property_id) in enumerate(keys_to_process, 1):
        print(f"[{i}/{len(keys_to_process)}] Объект ID: {property_id}, Ключ: {ical_key[:10]}...")
        
        # Проверка кэша
        if ical.is_cache_valid(ical_key, property_id):
            skipped += 1
            print("  ⏭ Кэш актуален")
            continue
        
        # Загрузка
        content = ical.download_ical(ical_key, property_id)
        
        if content:
            # Сохранение
            if ical.save_to_cache(ical_key, content, property_id):
                success += 1
                print(f"  ✅ Сохранен")
            else:
                errors += 1
                print(f"  ❌ Ошибка сохранения")
        else:
            errors += 1
            print(f"  ❌ Ошибка загрузки")
        
        # Задержка
        if i < len(keys_to_process):
            time.sleep(1)  # 1 секунда задержки между запросами
    
    # Результат
    result = {
        'total': len(keys_to_process),
        'success': success,
        'errors': errors,
        'skipped': skipped,
        'timestamp': time.time(),
        'filter': 'estate_property: publish only'
    }
    
    print("\n" + "="*60)
    print(f"ИТОГ: Всего={result['total']}, Успешно={success}, "
          f"Ошибки={errors}, Пропущено={skipped}")
    
    # Показываем файлы
    show_cache_files()
    
    return result

def show_cache_files():
    """Показать все файлы в кэше"""
    cache_dir = Path(config.CACHE_DIR)
    if cache_dir.exists():
        ics_files = list(cache_dir.glob("*.ics"))
        err_files = list(cache_dir.glob("*.err"))
        
        print(f"\n📁 Файлов в кэше: {len(ics_files)} .ics, {len(err_files)} .err")
        
        # Сортируем по времени (новые первыми)
        ics_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        err_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        if ics_files:
            print(f"\n✅ Файлы iCal ({len(ics_files)}):")
            for f in ics_files:  # Убрали срез [:5]
                size_kb = f.stat().st_size / 1024
                mtime = time.strftime('%Y-%m-%d %H:%M:%S', 
                                    time.localtime(f.stat().st_mtime))
                print(f"  - {f.name} ({size_kb:.1f} KB, {mtime})")
        
        if err_files:
            print(f"\n⚠ Файлы ошибок ({len(err_files)}):")
            for f in err_files:  # Убрали срез [:3]
                mtime = time.strftime('%Y-%m-%d %H:%M:%S', 
                                    time.localtime(f.stat().st_mtime))
                print(f"  - {f.name} ({mtime})")
        else:
            print(f"\n✅ Файлов ошибок нет")

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Синхронизация iCal файлов')
    parser.add_argument('--all', action='store_true', help='Все ключи')
    parser.add_argument('--test', type=int, default=5, help='Тест N ключей (по умолчанию 5)')
    
    args = parser.parse_args()
    
    if args.all:
        result = sync_keys()
    elif args.test:
        result = sync_keys(limit=args.test)
    else:
        parser.print_help()
        print("\nПримеры:")
        print("  python3 sync.py --all         # Все ключи")
        print("  python3 sync.py --test 3      # Тест 3 ключа")
        print("  python3 sync.py --test        # Тест 5 ключей")
        sys.exit(0)
    
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # Пример ссылки для Авито
    if result['success'] > 0:
        db = Database()
        keys = db.get_all_keys()
        if keys:
            ical_key = keys[0][0]
            print(f"\n🔗 Пример ссылки для Авито:")
            print(f"{config.WP_URL.rstrip('/')}/8j0rn/ical/{ical_key}.ics")