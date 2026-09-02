const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();

const state = {
  products: [],
  cart: JSON.parse(localStorage.getItem('normwear_cart') || '{}'),
  category: 'Все',
  query: '',
  view: 'catalog'
};

const app = document.getElementById('app');
const esc = s => String(s ?? '').replace(/[&<>\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const money = v => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0}).format(v) + ' ₽';
const saveCart = () => localStorage.setItem('normwear_cart', JSON.stringify(state.cart));
const cartCount = () => Object.values(state.cart).reduce((a, b) => a + b.qty, 0);

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
    <main>${filtered.length ? filtered.map(productCard).join('') : '<div class="empty">Ничего не найдено</div>'}</main>
    <nav>
      <span onclick="render()" style="color:var(--accent)">⌂<small>Главная</small></span>
      <span onclick="showFavorites()">♡<small>Избранное</small></span>
      <span onclick="showCart()">🛒<small>Корзина${cartCount() ? ` (${cartCount()})` : ''}</small></span>
      <span onclick="showProfile()">◉<small>Профиль</small></span>
    </nav>
  </div>`;

  document.getElementById('q').oninput = e => { state.query = e.target.value; render(); };
}

// ── PRODUCT DETAIL ──

window.showProduct = id => {
  const p = state.products.find(x => x.id === id);
  if (!p) return;
  state.view = 'detail';
  const sizes = JSON.parse(p.sizes_json || '[]');

  app.innerHTML = `<div class="app detail">
    <button class="back" onclick="render()">← Назад</button>
    <div class="gallery"><img src="${p.media?.[0] || ''}" onerror="this.style.display='none'"></div>
    <div class="info">
      <h2>${esc(p.title)}</h2>
      <div class="p">${money(p.price)}</div>
      ${p.description ? `<div class="desc">${esc(p.description)}</div>` : ''}
    </div>
    ${sizes.length ? `<div class="sizes">${sizes.map((s, i) => `<button class="${i === 0 ? 'active' : ''}" onclick="selectSize(this)">${esc(s)}</button>`).join('')}</div>` : ''}
    <button class="buy" onclick="addToCart(${p.id})" style="padding:14px;font-size:15px">В корзину — ${money(p.price)}</button>
    <nav>
      <span onclick="render()">⌂<small>Главная</small></span>
      <span onclick="showFavorites()">♡<small>Избранное</small></span>
      <span onclick="showCart()">🛒<small>Корзина${cartCount() ? ` (${cartCount()})` : ''}</small></span>
      <span onclick="showProfile()">◉<small>Профиль</small></span>
    </nav>
  </div>`;
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
  else showProduct(id);
};

window.setCategory = c => { state.category = c; render(); };

window.showCart = () => {
  state.view = 'cart';
  const items = Object.entries(state.cart)
    .map(([id, x]) => { const p = state.products.find(p => p.id == id); return p ? { ...x, p } : null; })
    .filter(Boolean);
  const total = items.reduce((s, x) => s + x.p.price * x.qty, 0);
  const u = tg?.initDataUnsafe?.user;

  app.innerHTML = `<div class="app">
    <header>
      <button class="back" onclick="render()">← Назад</button>
      <div class="brand">КОРЗИНА</div>
    </header>
    <main class="cart">${items.length ? items.map(x => `<div class="cartrow"><b>${esc(x.p.title)}</b><span>${x.qty} × ${money(x.p.price)}</span></div>`).join('') : '<div class="empty">Корзина пуста</div>'}</main>
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

// ── FAVORITES (заглушка) ──

window.showFavorites = () => {
  app.innerHTML = `<div class="app">
    <header>
      <button class="back" onclick="render()">← Назад</button>
      <div class="brand">ИЗБРАННОЕ</div>
    </header>
    <div class="empty">Скоро здесь будут ваши избранные товары</div>
    <nav>
      <span onclick="render()">⌂<small>Главная</small></span>
      <span onclick="showFavorites()" style="color:var(--accent)">♡<small>Избранное</small></span>
      <span onclick="showCart()">🛒<small>Корзина${cartCount() ? ` (${cartCount()})` : ''}</small></span>
      <span onclick="showProfile()">◉<small>Профиль</small></span>
    </nav>
  </div>`;
};

// ── PROFILE (заглушка) ──

window.showProfile = () => {
  const u = tg?.initDataUnsafe?.user;
  app.innerHTML = `<div class="app">
    <header>
      <button class="back" onclick="render()">← Назад</button>
      <div class="brand">ПРОФИЛЬ</div>
    </header>
    <div style="text-align:center;padding:40px 0">
      <div style="width:72px;height:72px;border-radius:50%;background:var(--bg3);margin:0 auto 16px;display:flex;align-items:center;justify-content:center;font-size:32px">${u?.first_name?.[0] || '👤'}</div>
      <div style="font-size:18px;font-weight:700;margin-bottom:4px">${esc(u?.first_name || 'Гость')} ${esc(u?.last_name || '')}</div>
      <div style="color:var(--text2);font-size:14px">@${esc(u?.username || '—')}</div>
    </div>
    <nav>
      <span onclick="render()">⌂<small>Главная</small></span>
      <span onclick="showFavorites()">♡<small>Избранное</small></span>
      <span onclick="showCart()">🛒<small>Корзина${cartCount() ? ` (${cartCount()})` : ''}</small></span>
      <span onclick="showProfile()" style="color:var(--accent)">◉<small>Профиль</small></span>
    </nav>
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
