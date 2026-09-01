import os
os.environ.setdefault('SHOP_BOT_TOKEN','test')
os.environ.setdefault('ADMIN_BOT_TOKEN','test')
os.environ.setdefault('TELEGRAM_API_ID','123')
os.environ.setdefault('TELEGRAM_API_HASH','testhash')
os.environ.setdefault('ADMIN_TELEGRAM_IDS','1977604257')

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from app.main import Checkout
from app.market import MarketOffer, summarize
from app.pricing import recommend_price, MarketSnapshot
from decimal import Decimal

def test_checkout_rejects_empty_fields():
    for field in ['name','phone','city','address']:
        data = dict(lines=[{'product_id':1,'quantity':1}], name='Ivan', phone='+79990001122', city='Moscow', address='Lenina 1')
        data[field] = ''
        c = Checkout(**data)
        with pytest.raises(ValueError):
            c.validate_fields()

def test_checkout_rejects_short_phone():
    c = Checkout(lines=[{'product_id':1,'quantity':1}], name='Ivan', phone='123', city='Moscow', address='Lenina 1')
    with pytest.raises(ValueError):
        c.validate_fields()

def test_checkout_valid():
    c = Checkout(lines=[{'product_id':1,'quantity':2}], name='Ivan', phone='+79990001122', city='Moscow', address='Lenina 1')
    c.validate_fields()  # should not raise

def test_checkout_dedup_logic():
    c = Checkout(lines=[{'product_id':1,'quantity':10},{'product_id':1,'quantity':5}], name='Ivan', phone='+79990001122', city='Moscow', address='Lenina 1')
    merged = {}
    for line in c.lines:
        key=(line.product_id, line.size)
        merged[key]=merged.get(key,0)+line.quantity
    assert merged[(1,None)]==15

def test_market_median_fixed():
    offers=[MarketOffer('s1','a',Decimal('10000')), MarketOffer('s2','b',Decimal('12000')), MarketOffer('s3','c',Decimal('11000'))]
    s=summarize(offers)
    assert s.median==Decimal('11000')
    assert s.minimum==Decimal('10000')
    assert s.maximum==Decimal('12000')

def test_pricing_with_market():
    market=MarketSnapshot(median=Decimal('12000'), minimum=Decimal('10000'), maximum=Decimal('15000'), sample_size=5, sources=['avito'])
    decision=recommend_price(Decimal('8000'), Decimal('35'), market)
    # candidate = min(12000*0.985=11820,12000)=11820, floor=8000/0.65=12307 -> candidate<floor so floor wins rounded to 12310
    assert decision.price>=Decimal('12300')

def test_allowed_admin():
    import os
    os.environ['ADMIN_TELEGRAM_IDS']='1977604257'
    os.environ['SHOP_BOT_TOKEN']='t'
    os.environ['ADMIN_BOT_TOKEN']='t'
    os.environ['TELEGRAM_API_ID']='1'
    os.environ['TELEGRAM_API_HASH']='h'
    from importlib import reload
    import app.config
    reload(app.config)
    import app.bot_admin
    reload(app.bot_admin)
    assert app.bot_admin.allowed(1977604257) is True
    assert app.bot_admin.allowed(999) is False
    os.environ['ADMIN_TELEGRAM_IDS']=''
    reload(app.config)
    reload(app.bot_admin)
    assert app.bot_admin.allowed(1977604257) is False
