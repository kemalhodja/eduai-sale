# TalkCash

Sesli komut (NLP), akıllı klavye ve yapay zeka destekli kişisel finans ve yaşam yönetimi uygulaması.

## Mimari

```
talkcash/
├── backend/          # FastAPI + PostgreSQL + Redis + MinIO (S3)
│   └── alembic/      # DB migrations
├── mobile/           # React Native (Expo 52) — TR/EN i18n
├── scripts/          # deploy, release, APK build
└── docker-compose.yml
```

## Özellikler

- JWT Auth + refresh token + PIN + Biyometrik
- Sesli komut (Whisper) + Türkçe NLP + slash komutlar
- OCR fiş tarama + S3/MinIO arşivi
- Çoklu cüzdan + döviz kuru sync
- Bütçe, ajanda, alışveriş, AI mentor (LLM chat)
- Sosyal (borç, split, ortak kasa WS, sahiplik devri)
- Offline kuyruk + optimistic snapshot (cüzdan, işlem, ajanda, alışveriş, sesli komut)

### Çevrimdışı senkron

- Yazma işlemleri ağ/5xx hatasında `mobile/services/offlineQueue.ts` kuyruğuna alınır.
- Anında UI güncellemesi için `mobile/services/syncCache.ts` optimistic snapshot kullanır.
- Uygulama ön plana gelince `useOfflineSync` kuyruğu boşaltır; Ayarlar’dan manuel sync de mümkün.
- Oturum süresi dolunca kuyruk korunur; çıkış yaparken bekleyen işlem varsa uyarı gösterilir.
- Zincirli offline işlemler (kasa oluştur → gelir ekle) client/server ID remapping ile senkronize edilir.
- Bütçe CRUD çevrimdışı kuyruğa alınabilir (`budget_create/update/delete`); snapshot'ta optimistic güncelleme.
- Alışveriş ekleme + tamamlama zinciri `client_item_ids` ile aynı batch'te senkronize edilir.
- Push bildirim + deep link + geofencing
- PDF/Excel export
- **Çoklu dil**: Türkçe + English

## Hızlı Başlangıç

```bash
docker compose up -d
cd mobile && npm install && cp .env.example .env && npx expo start --tunnel
```

- API: http://localhost:8000/docs
- Health: http://localhost:8000/health
- MinIO Console: http://localhost:9001 (talkcash / talkcash123)

**Android telefon:** `./scripts/phone-setup.sh` → `./scripts/build-android-apk.sh --staging --wait --download` — [docs/ANDROID_APK.md](docs/ANDROID_APK.md)

## Testler

```bash
cd backend && RATE_LIMIT_ENABLED=false SCHEDULER_ENABLED=false python3 -m pytest tests/ -q
cd mobile && npm test && npx tsc --noEmit
./scripts/verify-release.sh
```

CI: GitHub Actions `main` branch push'ta otomatik çalışır.

## Release

**İlk kurulum:** [docs/SETUP_RELEASE.md](docs/SETUP_RELEASE.md) · [.github/RELEASE_SECRETS_CHECKLIST.md](.github/RELEASE_SECRETS_CHECKLIST.md)

GitHub → **Actions** → **Release Production (Full Pipeline)** → `confirm`: `release`

| Rehber | İçerik |
|--------|--------|
| [docs/SETUP_RELEASE.md](docs/SETUP_RELEASE.md) | İlk release kurulumu (secrets checklist) |
| [docs/DEPLOY_TARGET.md](docs/DEPLOY_TARGET.md) | **Deploy hedefi: Render** (doğrulanmış tek kaynak) |
| [docs/PLAY_STORE_LISTING.md](docs/PLAY_STORE_LISTING.md) | Mağaza metinleri + checklist |
| [docs/PRIVACY.md](docs/PRIVACY.md) | Gizlilik politikası |
| [docs/TERMS.md](docs/TERMS.md) | Kullanım ve abonelik şartları |

## Deploy

**Production API:** Render — `https://talkcash-api-prod.onrender.com`  
**Mekanizma:** `render.yaml` Blueprint (`autoDeploy: true`, branch: `main`) veya
`.github/workflows/render-deploy.yml` deploy hook'u.

```bash
# main'e merge -> Render otomatik deploy eder.
# Manuel tetikleme:
git push origin main            # autoDeploy
# veya GitHub Actions -> render-deploy.yml -> Run workflow
```

> Not: Fly.io referansları geçersizdir (legacy). Production yalnızca Render'dır;
> `setup-fly-prod.sh` çalışmaz durumdadır ve dokümanlarda tutulmamalıdır.

### Prod ortam değişkenleri (Render Dashboard)

Zorunlular: `SECRET_KEY` (32+), `DATABASE_URL`, `REDIS_URL`, `SMTP_*`,
`GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`. Mock flag'leri kapalı olmalı:
`BILLING_PREMIUM_UNLOCKED=false`, `GOOGLE_PLAY_VERIFY_MOCK=false`,
`APPLE_VERIFY_MOCK=false`. Uygulama açılışta doğrular (fail-fast).

Kendi VPS'inizde Docker ile çalıştırıyorsanız:

```bash
POSTGRES_PASSWORD=... REDIS_PASSWORD=... MINIO_ROOT_PASSWORD=... SECRET_KEY=... \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

(Prod overlay şifreleri zorunlu kılar; DB/Redis/MinIO portları yalnızca
127.0.0.1'e bağlanır; MinIO bucket anonim okumaya kapalıdır.)

## Native Build (Siri & Google App Actions)

Expo Go Siri/App Actions desteklemez — development client gerekir:

```bash
cd mobile
npx eas login
eas build --profile development --platform android
```

## Veritabanı Migration

```bash
cd backend && alembic upgrade head
```

TalkCash © 2026
