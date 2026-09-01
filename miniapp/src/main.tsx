import React from "react"
import { createRoot } from "react-dom/client"
import "./style.css"

const products = [
  {id:1,title:"Nike Dunk Low Panda",price:"8 490 ₽",image:"https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800"},
  {id:2,title:"Adidas Campus 00s",price:"7 990 ₽",image:"https://images.unsplash.com/photo-1525966222134-fcfa99b8ae77?w=800"},
  {id:3,title:"New Balance 530",price:"8 790 ₽",image:"https://images.unsplash.com/photo-1552346154-21d32810aba3?w=800"},
  {id:4,title:"Nike Air Force 1",price:"7 490 ₽",image:"https://images.unsplash.com/photo-1549298916-b41d501d3772?w=800"}
]

function App(){
  return <div className="app">
    <header>
      <div className="brand">NORMWEAR</div>
      <div className="search">⌕ <span>Поиск товаров</span></div>
    </header>
    <section className="hero"><small>NEW DROP</small><h1>Новая коллекция</h1><p>Актуальные модели. Быстрая покупка прямо в Telegram.</p></section>
    <div className="chips"><button>Все</button><button>Кроссовки</button><button>Одежда</button><button>Аксессуары</button></div>
    <main>{products.map(p=><article className="card" key={p.id}><img src={p.image}/><div className="meta"><div className="title">{p.title}</div><div className="price">{p.price}</div><button className="buy">Добавить в корзину</button></div></article>)}</main>
    <nav><span>⌂<small>Главная</small></span><span>♡<small>Избранное</small></span><span>🛒<small>Корзина</small></span><span>◉<small>Профиль</small></span></nav>
  </div>
}
createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>)
