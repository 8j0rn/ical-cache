#!/usr/bin/env python3
"""
check_missing_ical.py - Проверка объектов без iCal ключей
"""
import os
import sys
import json
import csv
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from app import Database, config

def check_missing_ical_keys():
    """Проверка и вывод объектов без iCal ключей"""
    db = Database()
    
    print("🔍 Поиск объектов без iCal ключей...")
    print("=" * 80)
    print("Условия поиска:")
    print("- post_type: estate_property")
    print("- post_status: publish")
    print("- НЕТ meta_key: 'unique_code_ica'")
    print("=" * 80)
    
    # Получаем объекты без iCal ключей
    properties_without_ical = db.get_properties_without_ical()
    
    if not properties_without_ical:
        print("✅ Все опубликованные объекты имеют iCal ключи!")
        return []
    
    print(f"\n📊 Найдено объектов БЕЗ iCal ключей: {len(properties_without_ical)}")
    print("=" * 80)
    
    # Выводим список на экран
    print("\n📋 Список объектов без iCal ключей:")
    print("-" * 80)
    print(f"{'ID':<8} | {'Дата создания':<16} | {'Заголовок'}")
    print("-" * 80)
    
    for i, (property_id, title, post_date) in enumerate(properties_without_ical, 1):
        # Обрезаем длинные заголовки
        short_title = (title[:50] + "...") if title and len(title) > 50 else title
        # Форматируем дату
        date_str = post_date.strftime('%Y-%m-%d') if isinstance(post_date, datetime) else str(post_date)
        
        print(f"{property_id:<8} | {date_str:<16} | {short_title}")
    
    print("-" * 80)
    
    return properties_without_ical

def save_to_file(properties, format='txt'):
    """Сохранение результатов в файл"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    if format == 'json':
        filename = f"missing_ical_{timestamp}.json"
        data = []
        for prop_id, title, post_date in properties:
            data.append({
                'id': prop_id,
                'title': title,
                'post_date': post_date.strftime('%Y-%m-%d %H:%M:%S') if isinstance(post_date, datetime) else str(post_date),
                'edit_url': f"{config.WP_URL}/wp-admin/post.php?post={prop_id}&action=edit"
            })
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        print(f"📁 Результаты сохранены в JSON файл: {filename}")
        
    elif format == 'csv':
        filename = f"missing_ical_{timestamp}.csv"
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['ID', 'Заголовок', 'Дата создания', 'URL редактирования'])
            
            for prop_id, title, post_date in properties:
                edit_url = f"{config.WP_URL}/wp-admin/post.php?post={prop_id}&action=edit"
                date_str = post_date.strftime('%Y-%m-%d') if isinstance(post_date, datetime) else str(post_date)
                writer.writerow([prop_id, title, date_str, edit_url])
                
        print(f"📁 Результаты сохранены в CSV файл: {filename}")
        
    else:  # txt
        filename = f"missing_ical_{timestamp}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"Объекты без iCal ключей - проверка от {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n")
            f.write(f"Всего найдено: {len(properties)}\n\n")
            
            f.write(f"{'ID':<8} | {'Дата':<16} | {'Заголовок':<50} | {'Ссылка на редактирование'}\n")
            f.write("-" * 100 + "\n")
            
            for prop_id, title, post_date in properties:
                edit_url = f"{config.WP_URL}/wp-admin/post.php?post={prop_id}&action=edit"
                date_str = post_date.strftime('%Y-%m-%d') if isinstance(post_date, datetime) else str(post_date)
                short_title = (title[:47] + "...") if title and len(title) > 50 else title
                
                f.write(f"{prop_id:<8} | {date_str:<16} | {short_title:<50} | {edit_url}\n")
                
        print(f"📁 Результаты сохранены в текстовый файл: {filename}")
    
    return filename

def get_statistics():
    """Получение статистики по iCal ключам"""
    db = Database()
    
    print("\n📈 Статистика по iCal ключам:")
    print("=" * 60)
    
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 1. Всего опубликованных объектов недвижимости
        cursor.execute("""
            SELECT COUNT(*) 
            FROM wp_posts 
            WHERE post_type = 'estate_property' 
              AND post_status = 'publish'
        """)
        total_published = cursor.fetchone()[0]
        
        # 2. Объектов С iCal ключами
        cursor.execute("""
            SELECT COUNT(DISTINCT p.ID)
            FROM wp_posts p
            INNER JOIN wp_postmeta pm ON p.ID = pm.post_id
            WHERE p.post_type = 'estate_property'
              AND p.post_status = 'publish'
              AND pm.meta_key = 'unique_code_ica'
              AND pm.meta_value != ''
        """)
        with_ical = cursor.fetchone()[0]
        
        # 3. Объектов БЕЗ iCal ключей
        without_ical = total_published - with_ical
        
        # 4. Процент покрытия
        coverage_pct = (with_ical / total_published * 100) if total_published > 0 else 0
        
        cursor.close()
        conn.close()
        
        print(f"Всего опубликованных объектов: {total_published}")
        print(f"С iCal ключами:                {with_ical}")
        print(f"Без iCal ключей:              {without_ical}")
        print(f"Покрытие iCal:                {coverage_pct:.1f}%")
        
        if without_ical > 0:
            print(f"\n⚠ Внимание: {without_ical} объектов без iCal ключей!")
            print("Эти объекты не будут синхронизироваться с Авито.")
            
        print("=" * 60)
        
        return {
            'total_published': total_published,
            'with_ical': with_ical,
            'without_ical': without_ical,
            'coverage_pct': coverage_pct
        }
        
    except Exception as e:
        print(f"Ошибка получения статистики: {e}")
        return {}

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Проверка объектов без iCal ключей')
    parser.add_argument('--save', choices=['txt', 'json', 'csv'], 
                       help='Сохранить результаты в файл (txt, json или csv)')
    parser.add_argument('--stats', action='store_true', 
                       help='Показать только статистику')
    parser.add_argument('--list', action='store_true', 
                       help='Показать только список (без статистики)')
    
    args = parser.parse_args()
    
    if args.stats:
        # Только статистика
        get_statistics()
    elif args.list:
        # Только список
        props = check_missing_ical_keys()
        if args.save:
            save_to_file(props, args.save)
    else:
        # Полный отчет
        props = check_missing_ical_keys()
        if props:
            stats = get_statistics()
            if args.save:
                save_to_file(props, args.save)