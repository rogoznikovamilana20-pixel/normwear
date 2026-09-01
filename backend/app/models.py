from datetime import datetime
from sqlalchemy import String, Integer, BigInteger, Boolean, DateTime, Numeric, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Product(Base):
    __tablename__ = 'products'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_chat: Mapped[str] = mapped_column(String(255), index=True)
    supplier_message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    supplier_grouped_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    sku: Mapped[str | None] = mapped_column(String(128), unique=True)
    brand: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(128), index=True)
    sizes_json: Mapped[str] = mapped_column(Text, default='[]')
    media_json: Mapped[str] = mapped_column(Text, default='[]')
    purchase_price: Mapped[float] = mapped_column(Numeric(12, 2))
    sale_price: Mapped[float] = mapped_column(Numeric(12, 2))
    price_confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default='pending', index=True)
    channel_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class MarketSnapshot(Base):
    __tablename__ = 'market_snapshots'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id', ondelete='CASCADE'), index=True)
    source: Mapped[str] = mapped_column(String(64))
    observed_price: Mapped[float] = mapped_column(Numeric(12, 2))
    title: Mapped[str] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Customer(Base):
    __tablename__ = 'customers'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = 'orders'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(32), default='awaiting_delivery', index=True)
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    delivery_cost: Mapped[float | None] = mapped_column(Numeric(12, 2))
    total: Mapped[float | None] = mapped_column(Numeric(12, 2))
    customer_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    payment_method: Mapped[str | None] = mapped_column(String(32))
    payment_status: Mapped[str] = mapped_column(String(32), default='unpaid')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class BannedProduct(Base):
    __tablename__ = 'banned_products'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    title_pattern: Mapped[str | None] = mapped_column(String(255), index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class PromoCode(Base):
    __tablename__ = 'promo_codes'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    discount_type: Mapped[str] = mapped_column(String(16))
    discount_value: Mapped[float] = mapped_column(Numeric(12, 2))
    min_order: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    max_uses: Mapped[int] = mapped_column(Integer, default=1000)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Favorite(Base):
    __tablename__ = 'favorites'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id', ondelete='CASCADE'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Review(Base):
    __tablename__ = 'reviews'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id', ondelete='CASCADE'), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey('orders.id', ondelete='SET NULL'))
    rating: Mapped[int] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default='pending')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class OrderItem(Base):
    __tablename__ = 'order_items'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('orders.id', ondelete='CASCADE'), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id', ondelete='RESTRICT'))
    title: Mapped[str] = mapped_column(String(255))
    size: Mapped[str | None] = mapped_column(String(32))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2))

class SupportTicket(Base):
    __tablename__ = 'support_tickets'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    admin_chat_id: Mapped[int] = mapped_column(BigInteger)
    admin_message_id: Mapped[int] = mapped_column(Integer)
    user_message_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
