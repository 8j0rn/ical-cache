#!/usr/bin/env python3
"""
ical_stats.py - Логирование и статистика запросов iCal файлов
"""
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

class ICalStats:
    """Система статистики запросов iCal файлов"""
    
    _log_file = Path(__file__).parent / 'data' / 'logs' / 'ical_requests.log'
    _stats_file = Path(__file__).parent / 'data' / 'logs' / 'ical_stats.json'
    
    @classmethod
    def log_request(cls, filename, client_ip, user_agent, status='success'):
        """Логирование запроса iCal файла"""
        try:
            cls._log_file.parent.mkdir(parents=True, exist_ok=True)
            
            log_entry = {
                'timestamp': time.time(),
                'datetime': datetime.now().isoformat(),
                'filename': filename,
                'client_ip': client_ip,
                'user_agent': user_agent,
                'status': status
            }
            
            with open(cls._log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                
        except Exception as e:
            print(f"[ICalStats] Ошибка логирования: {e}")
    
    @classmethod
    def get_stats(cls, days=7):
        """Получение статистики за последние N дней"""
        try:
            if not cls._log_file.exists():
                return {}
            
            cutoff = time.time() - (days * 86400)
            stats = {
                'total_requests': 0,
                'successful': 0,
                'failed': 0,
                'by_filename': defaultdict(int),
                'by_day': defaultdict(int),
                'by_ip': defaultdict(int),
                'recent_requests': []
            }
            
            with open(cls._log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if entry['timestamp'] < cutoff:
                            continue
                        
                        stats['total_requests'] += 1
                        
                        if entry['status'] == 'success':
                            stats['successful'] += 1
                        else:
                            stats['failed'] += 1
                        
                        stats['by_filename'][entry['filename']] += 1
                        
                        # Группировка по дате
                        date = datetime.fromtimestamp(entry['timestamp']).strftime('%Y-%m-%d')
                        stats['by_day'][date] += 1
                        
                        # Группировка по IP
                        stats['by_ip'][entry['client_ip']] += 1
                        
                        # Последние 50 запросов
                        if len(stats['recent_requests']) < 50:
                            stats['recent_requests'].append(entry)
                            
                    except:
                        continue
            
            # Сортируем последние запросы (новые первыми)
            stats['recent_requests'].sort(key=lambda x: x['timestamp'], reverse=True)
            
            return stats
            
        except Exception as e:
            print(f"[ICalStats] Ошибка получения статистики: {e}")
            return {}
    
    @classmethod
    def cleanup_old_logs(cls, days=30):
        """Очистка старых логов"""
        try:
            if not cls._log_file.exists():
                return 0
            
            cutoff = time.time() - (days * 86400)
            temp_file = cls._log_file.with_suffix('.tmp')
            
            deleted = 0
            kept = 0
            
            with open(cls._log_file, 'r', encoding='utf-8') as f_in, \
                 open(temp_file, 'w', encoding='utf-8') as f_out:
                for line in f_in:
                    try:
                        entry = json.loads(line.strip())
                        if entry['timestamp'] >= cutoff:
                            f_out.write(line)
                            kept += 1
                        else:
                            deleted += 1
                    except:
                        continue
            
            temp_file.replace(cls._log_file)
            return deleted
            
        except Exception as e:
            print(f"[ICalStats] Ошибка очистки логов: {e}")
            return 0