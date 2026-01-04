import pickle
from pathlib import Path
from datetime import datetime, timedelta

class SessionStorage:
    _sessions = {}
    _session_file = Path(__file__).parent / 'data' / 'sessions.pkl'
    
    @classmethod
    def load_sessions(cls):
        """Загрузка сессий из файла"""
        try:
            if cls._session_file.exists():
                with open(cls._session_file, 'rb') as f:
                    cls._sessions = pickle.load(f)
        except:
            cls._sessions = {}
    
    @classmethod
    def save_sessions(cls):
        """Сохранение сессий в файл"""
        try:
            cls._session_file.parent.mkdir(exist_ok=True)
            with open(cls._session_file, 'wb') as f:
                pickle.dump(cls._sessions, f)
        except:
            pass
    
    @classmethod
    def get(cls, session_id):
        return cls._sessions.get(session_id)
    
    @classmethod
    def set(cls, session_id, data):
        cls._sessions[session_id] = data
        cls.save_sessions()
    
    @classmethod
    def delete(cls, session_id):
        if session_id in cls._sessions:
            del cls._sessions[session_id]
            cls.save_sessions()

# Создаем экземпляр
session_storage = SessionStorage()
session_storage.load_sessions()