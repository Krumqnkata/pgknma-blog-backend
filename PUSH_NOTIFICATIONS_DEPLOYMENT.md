# Web Push deployment

## 1. Миграции и зависимости

```bash
cd /var/www/blog/cms/cms
source ../venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py check
```

## 2. Фонова команда

Командата публикува чакащите планирани известия, напомня за събития и повтаря временно неуспешните Push доставки:

```bash
python manage.py dispatch_scheduled_notifications
```

За автоматично изпълнение:

```bash
sudo cp deployment/systemd/pgknma-notifications.service /etc/systemd/system/
sudo cp deployment/systemd/pgknma-notifications.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pgknma-notifications.timer
systemctl list-timers pgknma-notifications.timer
```

Ако приложението е разположено в различна директория или работи с различен Linux потребител, първо се коригират `WorkingDirectory`, `ExecStart`, `User` и `Group` в service файла.

## 3. Cloudflare

Пътищата `/api/push/config/` и `/api/push/subscription/` трябва да връщат JSON и за посетител без вход. В Cloudflare не трябва да има Managed Challenge, JavaScript Challenge или кеширане за `/api/push/*`.

Създайте WAF правило с условие:

```text
http.request.uri.path starts_with "/api/push/"
```

и го изключете от правилата, които налагат browser challenge. Добавете Cache Rule с действие `Bypass cache` за същия път. Това не изключва Django throttling, проверката на management token или останалите server-side защити.

Проверка:

```bash
curl -i https://pgknma.space/api/push/config/
```

Очаква се HTTP 200 и JSON, а не HTML страница „Just a moment…“.

## 4. Apache

Съществуващото `ProxyPass /api/ http://127.0.0.1:8000/api/` вече обхваща Push API-то и не изисква отделен маршрут. След промяна на Apache конфигурация:

```bash
sudo apachectl configtest
sudo systemctl reload apache2
```
