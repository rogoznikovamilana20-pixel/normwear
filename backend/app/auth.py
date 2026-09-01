import hashlib, hmac, json, time
from urllib.parse import parse_qsl
from .config import settings

def validate_init_data(init_data: str, max_age: int = 86400) -> dict:
    if not init_data:
        raise ValueError('Missing Telegram initData')
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop('hash', None)
    auth_date = int(pairs.get('auth_date', '0'))
    if not received_hash or not auth_date or time.time() - auth_date > max_age:
        raise ValueError('Invalid or expired initData')
    data_check = '\n'.join(f'{k}={v}' for k, v in sorted(pairs.items()))
    secret = hmac.new(b'WebAppData', settings.shop_bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise ValueError('Invalid initData signature')
    if 'user' in pairs:
        pairs['user'] = json.loads(pairs['user'])
    return pairs
