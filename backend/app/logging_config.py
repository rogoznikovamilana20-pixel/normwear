import logging, sys, json
from datetime import datetime, timezone

class StructuredFormatter(logging.Formatter):
    def format(self, record):
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        parts = [f'[{ts}]', f'[{record.levelname}]', f'[{record.name}]']
        msg = record.getMessage()
        if hasattr(record, 'extra_data'):
            parts.append(json.dumps(record.extra_data, default=str, ensure_ascii=False))
        parts.append(msg)
        return ' '.join(parts)

def setup_logging(level: str = 'INFO'):
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root.handlers.clear()
    root.addHandler(handler)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
