import logging
import psycopg2
from datetime import datetime
import json
from config import DB_CONFIG


class DatabaseLogger:
    """Logger that writes to news_parser_log table with 5000 record limit"""
    
    def __init__(self):
        self.max_records = 5000
    
    def _get_connection(self):
        """Get database connection"""
        try:
            return psycopg2.connect(
                host=DB_CONFIG['host'],
                database=DB_CONFIG['database'],
                user=DB_CONFIG['user'],
                password=DB_CONFIG['password'],
                port=DB_CONFIG['port']
            )
        except Exception as e:
            print(f"Failed to connect to database for logging: {e}")
            return None
    
    def _cleanup_old_logs(self, conn):
        """Keep only the latest 5000 log entries"""
        try:
            cursor = conn.cursor()
            # Delete old records, keeping only the latest 5000
            cursor.execute("""
                DELETE FROM news_parser_log
                WHERE id NOT IN (
                    SELECT id FROM news_parser_log
                    ORDER BY log_time DESC
                    LIMIT %s
                );
            """, (self.max_records,))
            deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            return deleted
        except Exception as e:
            print(f"Failed to cleanup old logs: {e}")
            return 0
    
    def log(self, log_level, event_type, message, article_link=None, details=None):
        """
        Write a log entry to the database
        
        Args:
            log_level: INFO, WARNING, ERROR
            event_type: FETCH_START, FETCH_COMPLETE, ARTICLE_FETCHED, ARTICLE_ERROR, DB_SAVE, etc.
            message: Log message
            article_link: Optional article URL
            details: Optional dictionary with additional details
        """
        conn = self._get_connection()
        if not conn:
            # Fallback to console logging if DB is unavailable
            print(f"[{log_level}] {event_type}: {message}")
            return False
        
        try:
            cursor = conn.cursor()
            
            # Convert details to JSON
            details_json = json.dumps(details) if details else None
            
            # Insert log entry
            cursor.execute("""
                INSERT INTO news_parser_log (log_time, log_level, event_type, message, article_link, details)
                VALUES (NOW(), %s, %s, %s, %s, %s)
            """, (log_level, event_type, message, article_link, details_json))
            
            conn.commit()
            
            # Periodically cleanup old logs (every 100 inserts)
            import random
            if random.randint(1, 100) == 1:
                self._cleanup_old_logs(conn)
            
            cursor.close()
            conn.close()
            return True
            
        except Exception as e:
            print(f"Failed to write log to database: {e}")
            print(f"[{log_level}] {event_type}: {message}")
            return False
    
    def info(self, event_type, message, article_link=None, details=None):
        """Log INFO level message"""
        return self.log('INFO', event_type, message, article_link, details)
    
    def warning(self, event_type, message, article_link=None, details=None):
        """Log WARNING level message"""
        return self.log('WARNING', event_type, message, article_link, details)
    
    def error(self, event_type, message, article_link=None, details=None):
        """Log ERROR level message"""
        return self.log('ERROR', event_type, message, article_link, details)


class DBHandler(logging.Handler):
    """Standard logging.Handler that writes records to news_parser_log via DatabaseLogger."""

    def __init__(self, event_type: str = "APP"):
        super().__init__()
        self._db_logger = DatabaseLogger()
        self._event_type = event_type

    def emit(self, record: logging.LogRecord):
        level_map = {
            logging.DEBUG: "INFO",
            logging.INFO: "INFO",
            logging.WARNING: "WARNING",
            logging.ERROR: "ERROR",
            logging.CRITICAL: "ERROR",
        }
        db_level = level_map.get(record.levelno, "INFO")
        msg = self.format(record)
        self._db_logger.log(db_level, self._event_type, msg)

