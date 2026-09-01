# Implementation checklist

## Phase 1 — foundation
- [x] Docker Compose
- [x] FastAPI skeleton
- [x] PostgreSQL models
- [x] Shop bot skeleton
- [x] Admin bot skeleton
- [x] Mini App storefront skeleton
- [x] Supplier parser boundary
- [x] Pricing engine boundary

## Phase 2 — supplier ingestion
- [ ] Authorize Telethon user session locally
- [ ] Fetch last 14 days
- [ ] Detect albums/grouped media and compare against supplier avatar/logo when available
- [x] Do NOT discard first photo positionally; keep all product media by default
- [ ] Deduplicate by source message/group + normalized SKU
- [ ] Detect continuation posts
- [ ] Persist raw source payload and normalized product

## Phase 3 — market intelligence
- [ ] Add compliant Avito data provider/API if available
- [ ] Add compliant Telegram competitor provider
- [ ] Normalize comparable offers
- [ ] Confidence score
- [ ] Price floor / target margin / market positioning
- [ ] Human approval below confidence threshold

## Phase 4 — publishing
- [ ] Upload product media to Telegram
- [ ] Generate consistent post copy
- [ ] Publish to @normwear_shop
- [ ] Attach Mini App deep-link button
- [ ] Store channel message ID
- [ ] Update/remove sold-out products

## Phase 5 — commerce
- [ ] Product API
- [ ] Cart
- [ ] Checkout
- [ ] Customer profile
- [ ] Orders
- [ ] Delivery marked manually by staff
- [ ] Payment provider integration
- [ ] Order notifications

## Phase 6 — admin
- [ ] Pending products
- [ ] Approve/edit/reject
- [ ] Orders
- [ ] Delivery cost input
- [ ] Customer CRM
- [ ] Promo codes
- [ ] Analytics
- [ ] Broadcasts

## Phase 7 — autonomous mode
- [ ] Scheduled supplier sync
- [ ] Automated market refresh
- [ ] Dynamic repricing
- [ ] Abandoned-cart reminders
- [ ] Low-stock alerts
- [ ] Daily AI business report
