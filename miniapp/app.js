const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();

const state = {
  products: [],
  cart: JSON.parse(localStorage.getItem('normwear_cart') || '{}'),
  category: 'Все',
  query: '',
  view: 'catalog',
  favorites: [],
  orders: [],
  galleryIndex: 0,
  reviewsStats: {},
};

const app = document.getElementById('app');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = v => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0}).format(v) + ' ₽';
const saveCart = () => localStorage.setItem('normwear_cart', JSON.stringify(state.cart));
const cartCount = () => Object.values(state.cart).reduce((a, b) => a + b.qty, 0);
const nav = () => `<nav>
  <span onclick="render()" style="color:${state.view==='catalog'?'var(--accent)':''}">⌂<small>Главная</small></span>
  <span onclick="showFavorites()" style="color:${state.view==='favorites'?'var(--accent)':''}">♡<small>Избранное</small></span>
  <span onclick="showCart()">🛒<small>Корзина${cartCount()?` (${cartCount()})`:''}</small></span>
  <span onclick="showProfile()" style="color:${state.view==='profile'?'var(--accent)':''}">◉<small>Профиль</small></span>
</nav>`;

const SEARCH_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>`;

function productCard(p) {
  return `<article class="card" onclick="showProduct(${p.id})">
    <img loading="lazy" src="${p.media?.[0] || ''}" onerror="this.style.display='none'">
    <div class="meta">
      <div class="title">${esc(p.title)}</div>
      <div class="price">${money(p.price)}</div>
      <button class="buy" onclick="event.stopPropagation();addToCart(${p.id})">В корзину</button>
    </div>
  </article>`;
}

function render() {
  state.view = 'catalog';
  state.galleryIndex = 0;
  const cats = ['Все', ...new Set(state.products.map(p => p.category).filter(Boolean))];
  const filtered = state.products.filter(p =>
    (state.category === 'Все' || p.category === state.category) &&
    (!state.query || p.title.toLowerCase().includes(state.query.toLowerCase()))
  );

  app.innerHTML = `<div class="app">
    <header>
      <div class="brand">NORM<span>WEAR</span></div>
      <div class="search">${SEARCH_SVG}<input id="q" placeholder="Поиск товаров..." value="${esc(state.query)}"></div>
    </header>
    <section class="hero">
      <small>NEW DROP</small>
      <h1>Новая коллекция</h1>
      <p>Актуальные модели с заказом прямо внутри Telegram</p>
    </section>
    <div class="chips">${cats.map(c => `<button class="${c === state.category ? 'active' : ''}" onclick="setCategory('${esc(c)}')">${esc(c)}</button>`).join('')}</div>
    <div style="text-align:center;margin:8px 0"><a href="#" onclick="showPopular();return false" style="color:var(--accent);font-size:14px;text-decoration:none">🔥 Популярные товары</a></div>
    <main>${filtered.length ? filtered.map(productCard).join('') : '<div class="empty">Ничего не найдено</div>'}</main>
    ${nav()}
  </div>`;

  document.getElementById('q').oninput = e => { state.query = e.target.value; render(); };
}

// ── PRODUCT DETAIL WITH GALLERY ──

window.showProduct = id => {
  const p = state.products.find(x => x.id === id);
  if (!p) return;
  state.view = 'detail';
  state.galleryIndex = 0;
  const sizes = JSON.parse(p.sizes_json || '[]');
  const media = p.media || [];

  function renderDetail() {
    const img = media[state.galleryIndex] || '';
    const dots = media.length > 1 ? `<div class="gallery-dots">${media.map((_, i) => `<span class="dot ${i === state.galleryIndex ? 'active' : ''}" onclick="event.stopPropagation();galleryGo(${id},${i})"></span>`).join('')}</div>` : '';

    const reviewsHtml = state.reviewsStats[id]
      ? `<div style="margin-top:16px;padding:16px;background:var(--bg2);border-radius:12px">
          <div style="font-size:14px;color:var(--text2)">⭐ ${state.reviewsStats[id].avg_rating} · ${state.reviewsStats[id].count} отзывов</div>
        </div>`
      : '';

    app.innerHTML = `<div class="app detail">
      <button class="back" onclick="render()">← Назад</button>
      <div class="gallery" onclick="galleryNext(${id})">
        <img src="${img}" onerror="this.style.display='none'">
        ${dots}
        ${media.length > 1 ? `<div class="gallery-counter">${state.galleryIndex + 1}/${media.length}</div>` : ''}
      </div>
      <div class="info">
        <h2>${esc(p.title)}</h2>
        <div class="p">${money(p.price)}</div>
        ${reviewsHtml}
        ${p.description ? `<div class="desc">${esc(p.description)}</div>` : ''}
      </div>
      ${sizes.length ? `<div class="sizes">${sizes.map((s, i) => `<button class="${i === 0 ? 'active' : ''}" onclick="selectSize(this)">${esc(s)}</button>`).join('')}</div>` : ''}
      ${sizes.length ? `<div style="text-align:center;margin-top:8px"><a href="#" onclick="showSizeGuide();return false" style="color:var(--text2);font-size:13px;text-decoration:underline">📏 Гид по размерам</a></div>` : ''}
      <button class="buy" onclick="addToCart(${p.id})" style="padding:14px;font-size:15px">В корзину — ${money(p.price)}</button>
      ${nav()}
    </div>`;
  }

  renderDetail();
  window._renderDetail = renderDetail;
  window._detailProduct = p;

  // Load reviews stats
  try {
    const r = await fetch(`/api/reviews-stats/${id}`);
    if (r.ok) {
      state.reviewsStats[id] = await r.json();
      window._renderDetail();
    }
  } catch (e) {}
};

window.galleryNext = id => {
  const p = window._detailProduct;
  if (!p) return;
  const media = p.media || [];
  if (media.length <= 1) return;
  state.galleryIndex = (state.galleryIndex + 1) % media.length;
  window._renderDetail();
};

window.galleryGo = (id, idx) => {
  state.galleryIndex = idx;
  window._renderDetail();
};

window.selectSize = btn => {
  btn.parentElement.querySelectorAll('button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
};

// ── CART ──

window.addToCart = id => {
  const p = state.products.find(x => x.id === id);
  const sizes = p ? JSON.parse(p.sizes_json || '[]') : [];
  const sizeEl = document.querySelector('.sizes button.active');
  const selectedSize = sizeEl ? sizeEl.textContent.trim() : (sizes[0] || null);
  state.cart[id] = state.cart[id] || { qty: 0, size: selectedSize };
  state.cart[id].qty++;
  if (selectedSize) state.cart[id].size = selectedSize;
  saveCart();
  tg?.HapticFeedback?.impactOccurred('light');
  if (state.view === 'catalog') render();
  else if (state.view === 'detail') window._renderDetail?.();
};

window.cartInc = id => {
  if (!state.cart[id]) return;
  state.cart[id].qty++;
  saveCart();
  showCart();
};

window.cartDec = id => {
  if (!state.cart[id]) return;
  state.cart[id].qty--;
  if (state.cart[id].qty <= 0) delete state.cart[id];
  saveCart();
  showCart();
};

window.cartRemove = id => {
  delete state.cart[id];
  saveCart();
  showCart();
};

window.setCategory = c => { state.category = c; render(); };

window.showCart = () => {
  state.view = 'cart';
  const items = Object.entries(state.cart)
    .map(([id, x]) => { const p = state.products.find(p => p.id == id); return p ? { ...x, p, id } : null; })
    .filter(Boolean);
  const total = items.reduce((s, x) => s + x.p.price * x.qty, 0);
  const u = tg?.initDataUnsafe?.user;

  app.innerHTML = `<div class="app">
    <header>
      <button class="back" onclick="render()">← Назад</button>
      <div class="brand">КОРЗИНА</div>
    </header>
    <main class="cart">${items.length ? items.map(x => `<div class="cartrow">
      <img src="${x.p.media?.[0] || ''}" class="cartrow-img" onerror="this.style.display='none'">
      <div class="cartrow-info">
        <b>${esc(x.p.title)}</b>
        ${x.size ? `<small>Размер: ${esc(x.size)}</small>` : ''}
        <div class="cartrow-controls">
          <button class="qty-btn" onclick="cartDec(${x.id})">−</button>
          <span class="qty-val">${x.qty}</span>
          <button class="qty-btn" onclick="cartInc(${x.id})">+</button>
          <button class="qty-btn qty-remove" onclick="cartRemove(${x.id})">✕</button>
        </div>
      </div>
      <div class="cartrow-price">${money(x.p.price * x.qty)}</div>
    </div>`).join('') : '<div class="empty">Корзина пуста</div>'}</main>
    ${items.length ? `<div class="checkout">
      <div class="total">Итого: <span id="total-display">${money(total)}</span></div>
      <div class="form">
        <input id="c_name" placeholder="Имя" value="${esc(u?.first_name || '')}">
        <input id="c_phone" placeholder="Телефон +7..." inputmode="tel">
        <input id="c_city" placeholder="Город">
        <input id="c_address" placeholder="Адрес (улица, дом, кв)">
        <div style="display:flex;gap:8px">
          <input id="c_promo" placeholder="Промокод" style="flex:1">
          <button onclick="applyPromo()" style="background:var(--accent);color:#000;border:0;border-radius:var(--radius-sm);padding:10px 14px;font-weight:700;cursor:pointer;font-family:inherit">ОК</button>
        </div>
        <div id="promo-result" style="font-size:13px;color:var(--accent);display:none"></div>
        <textarea id="c_comment" placeholder="Комментарий (необязательно)"></textarea>
      </div>
      <button class="checkoutbtn" onclick="checkout()">Оформить заказ</button>
    </div>` : ''}
    ${nav()}
  </div>`;
};

// ── CHECKOUT ──

let promoDiscount = 0;

window.applyPromo = async () => {
  const code = document.getElementById('c_promo')?.value?.trim();
  if (!code) return;
  try {
    const r = await fetch('/api/promo/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': tg.initData },
      body: JSON.stringify({ code })
    });
    const d = await r.json();
    const el = document.getElementById('promo-result');
    if (!r.ok) { el.style.display = 'block'; el.style.color = '#ff4444'; el.textContent = d.detail || 'Ошибка'; promoDiscount = 0; return; }
    el.style.display = 'block';
    promoDiscount = d.discount_value || 0;
    if (d.discount_type === 'percent') {
      const items = Object.entries(state.cart).map(([id, x]) => { const p = state.products.find(p => p.id == id); return p ? { ...x, p } : null; }).filter(Boolean);
      const total = items.reduce((s, x) => s + x.p.price * x.qty, 0);
      promoDiscount = Math.round(total * promoDiscount / 100);
      el.textContent = `✅ Скидка: -${money(promoDiscount)} (${d.discount_value}%)`;
    } else {
      el.textContent = `✅ Скидка: -${money(promoDiscount)}`;
    }
    const totalEl = document.getElementById('total-display');
    const items = Object.entries(state.cart).map(([id, x]) => { const p = state.products.find(p => p.id == id); return p ? { ...x, p } : null; }).filter(Boolean);
    const total = items.reduce((s, x) => s + x.p.price * x.qty, 0);
    totalEl.textContent = money(Math.max(0, total - promoDiscount));
  } catch (e) {}
};

window.checkout = async () => {
  const items = Object.entries(state.cart);
  if (!items.length) return;
  if (!tg?.initData) { alert('Откройте магазин из Telegram'); return; }

  const get = id => document.getElementById(id)?.value?.trim() || '';
  const name = get('c_name'), phone = get('c_phone'), city = get('c_city'), address = get('c_address'), comment = get('c_comment'), promo = get('c_promo');

  if (!name || !phone || !city || !address) {
    alert('Заполните имя, телефон, город и адрес');
    return;
  }

  const lines = items.map(([id, x]) => ({ product_id: Number(id), quantity: x.qty, size: x.size || null }));
  const payload = { lines, name, phone, city, address, comment: comment || null, payment_method: 'stars', promo_code: promo || null };

  const r = await fetch('/api/orders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': tg.initData },
    body: JSON.stringify(payload)
  });
  const d = await r.json();
  if (!r.ok) { alert(d.detail || 'Ошибка'); return; }

  if (d.invoice_link) {
    tg.openInvoice(d.invoice_link, async (status) => {
      if (status === 'paid') {
        state.cart = {};
        saveCart();
        app.innerHTML = `<div class="app success">
          <div class="mark">✓</div>
          <h1>Заказ #${d.order_id} оплачен</h1>
          <p>Звёзды списаны. Заказ в обработке.</p>
          <button class="buy" onclick="render()" style="max-width:200px">Вернуться в каталог</button>
        </div>`;
      } else {
        alert('Оплата не прошла. Попробуйте ещё раз.');
      }
    });
  } else {
    state.cart = {};
    saveCart();
    app.innerHTML = `<div class="app success">
      <div class="mark">✓</div>
      <h1>Заказ #${d.order_id} принят</h1>
      <p>Стоимость доставки менеджер рассчитает отдельно и сообщит итоговую сумму</p>
      <button class="buy" onclick="render()" style="max-width:200px">Вернуться в каталог</button>
    </div>`;
  }
};

// ── FAVORITES (real API) ──

window.toggleFav = async (id, e) => {
  if (e) e.stopPropagation();
  if (!tg?.initData) return;
  try {
    await fetch(`/api/favorites/${id}`, {
      method: 'POST',
      headers: { 'X-Telegram-Init-Data': tg.initData }
    });
    state.favorites = state.favorites.filter(x => x.id !== id);
    if (state.view === 'favorites') showFavorites();
  } catch (e) {}
};

window.showFavorites = async () => {
  state.view = 'favorites';
  state.favorites = [];
  if (tg?.initData) {
    try {
      const r = await fetch('/api/favorites', { headers: { 'X-Telegram-Init-Data': tg.initData } });
      if (r.ok) state.favorites = await r.json();
    } catch (e) {}
  }

  const favSet = new Set(state.favorites.map(f => f.id || f));
  const favProducts = state.products.filter(p => favSet.has(p.id));

  app.innerHTML = `<div class="app">
    <header>
      <button class="back" onclick="render()">← Назад</button>
      <div class="brand">ИЗБРАННОЕ</div>
    </header>
    <main>${favProducts.length ? favProducts.map(p => `<article class="card" onclick="showProduct(${p.id})">
      <img loading="lazy" src="${p.media?.[0] || ''}" onerror="this.style.display='none'">
      <div class="meta">
        <div class="title">${esc(p.title)}</div>
        <div class="price">${money(p.price)}</div>
        <button class="buy" onclick="event.stopPropagation();toggleFav(${p.id})" style="background:var(--bg3);color:var(--text2)">Убрать</button>
      </div>
    </article>`).join('') : '<div class="empty">Пока нет избранных товаров</div>'}</main>
    ${nav()}
  </div>`;
};

// ── PROFILE WITH ORDER HISTORY ──

window.showProfile = async () => {
  state.view = 'profile';
  const u = tg?.initDataUnsafe?.user;
  state.orders = [];
  if (tg?.initData) {
    try {
      const r = await fetch('/api/my-orders', { headers: { 'X-Telegram-Init-Data': tg.initData } });
      if (r.ok) state.orders = await r.json();
    } catch (e) {}
  }

  const statusEmoji = {'awaiting_payment':'💳','awaiting_delivery':'📦','paid':'✅','shipped':'🚚','assembling':'🔧','delivered':'🏁','in_transit':'🛵'};
  const ordersHtml = state.orders.length ? state.orders.map(o => {
    const em = statusEmoji[o.status] || '📋';
    const total = o.total != null ? money(o.total) : '—';
    return `<div class="orderrow" onclick="showOrderDetail(${o.id})">
      <div class="orderrow-left">
        <span class="order-status">${em}</span>
        <div><b>Заказ #${o.id}</b><small>${o.created_at || ''}</small></div>
      </div>
      <div class="orderrow-right">${total}</div>
    </div>`;
  }).join('') : '<div class="empty">Нет заказов</div>';

  app.innerHTML = `<div class="app">
    <header>
      <button class="back" onclick="render()">← Назад</button>
      <div class="brand">ПРОФИЛЬ</div>
    </header>
    <div style="text-align:center;padding:24px 0 16px">
      <div style="width:72px;height:72px;border-radius:50%;background:var(--bg3);margin:0 auto 16px;display:flex;align-items:center;justify-content:center;font-size:32px">${u?.first_name?.[0] || '👤'}</div>
      <div style="font-size:18px;font-weight:700;margin-bottom:4px">${esc(u?.first_name || 'Гость')} ${esc(u?.last_name || '')}</div>
      <div style="color:var(--text2);font-size:14px">@${esc(u?.username || '—')}</div>
    </div>
    <div style="margin-top:16px">
      <h3 style="font-size:16px;font-weight:700;margin-bottom:12px">Мои заказы</h3>
      ${ordersHtml}
    </div>
    <div style="margin-top:24px;display:flex;flex-direction:column;gap:12px">
      <button class="buy" onclick="window.open('https://t.me/norm_shop_bot?start=support','_blank')" style="width:100%">💬 Написать в поддержку</button>
      <button class="buy" onclick="showAbout()" style="width:100%;background:var(--bg3);color:var(--text2)">ℹ️ О магазине</button>
    </div>
    ${nav()}
  </div>`;
};

// ── ABOUT SHOP PAGE ──

window.showAbout = () => {
  state.view = 'about';
  app.innerHTML = `<div class="app">
    <header>
      <button class="back" onclick="showProfile()">← Назад</button>
      <div class="brand">О МАГАЗИНЕ</div>
    </header>
    <main style="padding:16px;line-height:1.7">
      <h2 style="font-size:20px;font-weight:800;margin-bottom:16px">NORMWEAR</h2>

      <h3 style="font-size:16px;font-weight:700;margin:20px 0 8px">🚚 Доставка</h3>
      <p style="color:var(--text2);font-size:14px">
        Доставка по Москве: 1-2 дня<br>
        По России: 3-7 дней<br>
        Самовывоз: бесплатно
      </p>

      <h3 style="font-size:16px;font-weight:700;margin:20px 0 8px">💳 Оплата</h3>
      <p style="color:var(--text2);font-size:14px">
        Telegram Stars (внутри Telegram)<br>
        СБП (qr-код)<br>
        Наложенный платёж
      </p>

      <h3 style="font-size:16px;font-weight:700;margin:20px 0 8px">🔄 Возврат</h3>
      <p style="color:var(--text2);font-size:14px">
        Возврат в течение 14 дней<br>
        Товар должен быть в оригинальной упаковке<br>
        Возврат денежных средств в течение 3 дней
      </p>

      <h3 style="font-size:16px;font-weight:700;margin:20px 0 8px">📞 Контакты</h3>
      <p style="color:var(--text2);font-size:14px">
        Telegram: @norm_shop_bot<br>
        Канал: @normwear_shop
      </p>
    </main>
    ${nav()}
  </div>`;
};

// ── ORDER DETAIL ──

window.showOrderDetail = async (orderId) => {
  state.view = 'order_detail';
  const order = state.orders.find(o => o.id === orderId);
  if (!order) { showProfile(); return; }

  const statusEmoji = {'awaiting_payment':'💳','awaiting_delivery':'📦','paid':'✅','shipped':'🚚','assembling':'🔧','delivered':'🏁','in_transit':'🛵'};
  const statusLabel = {'awaiting_payment':'Ожидает оплаты','awaiting_delivery':'Ожидает доставки','paid':'Оплачен','shipped':'Отправлен','assembling':'Собирается','delivered':'Доставлен','in_transit':'В пути'};

  const itemsHtml = (order.items || []).map(i => `
    <div class="orderrow">
      <div class="orderrow-left">
        <div><b>${esc(i.title || 'Товар')}</b><small>${i.size ? 'Размер: ' + i.size : ''} × ${i.quantity || 1}</small></div>
      </div>
      <div class="orderrow-right">${money(i.price || 0)}</div>
    </div>
  `).join('');

  app.innerHTML = `<div class="app">
    <header>
      <button class="back" onclick="showProfile()">← Назад</button>
      <div class="brand">ЗАКАЗ #${orderId}</div>
    </header>
    <main style="padding:16px">
      <div style="text-align:center;padding:20px 0">
        <div style="font-size:40px">${statusEmoji[order.status] || '📋'}</div>
        <div style="font-size:18px;font-weight:700;margin-top:8px">${statusLabel[order.status] || order.status}</div>
      </div>
      ${itemsHtml}
      <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--bg3);display:flex;justify-content:space-between;font-weight:700">
        <span>Итого</span><span>${money(order.total || 0)}</span>
      </div>
      <div style="margin-top:24px;display:flex;flex-direction:column;gap:12px">
        <button class="buy" onclick="window.open('https://t.me/norm_shop_bot?start=support','_blank')" style="width:100%">💬 Написать в поддержку</button>
      </div>
    </main>
    ${nav()}
  </div>`;
};

// ── SIZE GUIDE ──

window.showSizeGuide = () => {
  state.view = 'sizeguide';
  app.innerHTML = `<div class="app">
    <header>
      <button class="back" onclick="window._detailProduct ? showProduct(window._detailProduct.id) : render()">← Назад</button>
      <div class="brand">ГИД ПО РАЗМЕРАМ</div>
    </header>
    <main style="padding:16px;line-height:1.7">
      <h3 style="font-size:16px;font-weight:700;margin-bottom:12px">👔 Мужская одежда</h3>
      <table style="width:100%;font-size:13px;border-collapse:collapse;color:var(--text2)">
        <tr style="border-bottom:1px solid var(--bg3)"><td><b>Размер</b></td><td><b>Грудь</b></td><td><b>Талия</b></td></tr>
        <tr style="border-bottom:1px solid var(--bg3)"><td>S</td><td>88-92</td><td>73-78</td></tr>
        <tr style="border-bottom:1px solid var(--bg3)"><td>M</td><td>92-96</td><td>78-83</td></tr>
        <tr style="border-bottom:1px solid var(--bg3)"><td>L</td><td>96-100</td><td>83-88</td></tr>
        <tr style="border-bottom:1px solid var(--bg3)"><td>XL</td><td>100-104</td><td>88-93</td></tr>
        <tr><td>XXL</td><td>104-108</td><td>93-98</td></tr>
      </table>

      <h3 style="font-size:16px;font-weight:700;margin:20px 0 12px">👟 Обувь</h3>
      <table style="width:100%;font-size:13px;border-collapse:collapse;color:var(--text2)">
        <tr style="border-bottom:1px solid var(--bg3)"><td><b>EU</b></td><td><b>US</b></td><td><b>см</b></td></tr>
        <tr style="border-bottom:1px solid var(--bg3)"><td>38</td><td>5</td><td>24.0</td></tr>
        <tr style="border-bottom:1px solid var(--bg3)"><td>39</td><td>6</td><td>24.5</td></tr>
        <tr style="border-bottom:1px solid var(--bg3)"><td>40</td><td>7</td><td>25.0</td></tr>
        <tr style="border-bottom:1px solid var(--bg3)"><td>41</td><td>8</td><td>25.5</td></tr>
        <tr style="border-bottom:1px solid var(--bg3)"><td>42</td><td>9</td><td>26.5</td></tr>
        <tr style="border-bottom:1px solid var(--bg3)"><td>43</td><td>10</td><td>27.5</td></tr>
        <tr><td>44</td><td>11</td><td>28.0</td></tr>
      </table>
    </main>
    ${nav()}
  </div>`;
};

// ── POPULAR PRODUCTS ──

window.showPopular = async () => {
  state.view = 'popular';
  try {
    const r = await fetch('/api/popular');
    if (r.ok) state.popular = await r.json();
  } catch (e) { state.popular = []; }

  const items = (state.popular || []).map(p => `<article class="card" onclick="showProduct(${p.id})">
    <img loading="lazy" src="${p.media?.[0] || ''}" onerror="this.style.display='none'">
    <div class="meta">
      <div class="title">${esc(p.title)}</div>
      <div class="price">${money(p.price)}</div>
      <button class="buy" onclick="event.stopPropagation();addToCart(${p.id})">В корзину</button>
    </div>
  </article>`).join('');

  app.innerHTML = `<div class="app">
    <header>
      <button class="back" onclick="render()">← Назад</button>
      <div class="brand">ПОПУЛЯРНЫЕ</div>
    </header>
    <main>${items || '<div class="empty">Пока нет данных</div>'}</main>
    ${nav()}
  </div>`;
};

// ── INIT ──

(async () => {
  try {
    const r = await fetch('/api/products?limit=50');
    state.products = await r.json();
  } catch (e) {
    state.products = [];
  }
  render();
})();
