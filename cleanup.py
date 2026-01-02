#!/usr/bin/env python3
"""
Очистка старых файлов
"""
import os
import time
from pathlib import Path
from datetime import datetime, timedelta

from app import config, logger

def cleanup_old_files():
    """Очистка старых кэш-файлов"""
    logger.info("Начало очистки старых файлов")
    
    cutoff_time = time.time() - (config.CACHE_CLEANUP_DAYS * 86400)
    deleted_count = 0
    deleted_size = 0
    
    for cache_file in config.CACHE_DIR.glob("*.ics"):
        if cache_file.stat().st_mtime < cutoff_time:
            try:
                file_size = cache_file.stat().st_size
                cache_file.unlink()
                deleted_count += 1
                deleted_size += file_size
                logger.debug(f"Удален: {cache_file.name}")
            except Exception as e:
                logger.warning(f"Не удалось удалить {cache_file.name}: {e}")
    
    if deleted_count:
        logger.info(f"Очистка завершена. Удалено {deleted_count} файлов, {deleted_size/1024/1024:.2f} MB")
    else:
        logger.info("Очистка завершена. Старых файлов не найдено")
    
    return {
        'deleted_count': deleted_count,
        'deleted_size_mb': deleted_size / 1024 / 1024
    }

def get_stats():
    """Получение статистики кэша"""
    cache_files = list(config.CACHE_DIR.glob("*.ics"))
    total_size = sum(f.stat().st_size for f in cache_files)
    
    # Самые старые и новые файлы
    if cache_files:
        oldest = min(cache_files, key=lambda x: x.stat().st_mtime)
        newest = max(cache_files, key=lambda x: x.stat().st_mtime)
        
        stats = {
            'total_files': len(cache_files),
            'total_size_mb': total_size / 1024 / 1024,
            'oldest_file': {
                'name': oldest.name,
                'modified': datetime.fromtimestamp(oldest.stat().st_mtime).isoformat()
            },
            'newest_file': {
                'name': newest.name,
                'modified': datetime.fromtimestamp(newest.stat().st_mtime).isoformat()
            }
        }
    else:
        stats = {
            'total_files': 0,
            'total_size_mb': 0,
            'message': 'Кэш пуст'
        }
    
    return stats

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Утилиты очистки')
    parser.add_argument('--cleanup', action='store_true', help='Очистить старые файлы')
    parser.add_argument('--stats', action='store_true', help='Показать статистику')
    
    args = parser.parse_args()
    
    if args.cleanup:
        result = cleanup_old_files()
        print(f"Удалено {result['deleted_count']} файлов")
    elif args.stats:
        stats = get_stats()
        import json
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    else:
        parser.print_help()