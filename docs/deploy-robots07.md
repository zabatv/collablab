# Развёртывание на robots07.com

Схема простая: nginx отдаёт статику сайта и проксирует `/api` на Flask,
который слушает только `127.0.0.1` и наружу не торчит.

```
браузер ──https──> nginx ─┬─ /            → frontend/ (файлы)
                          ├─ /uploads/    → frontend/uploads/ (фото, видео)
                          └─ /api/        → 127.0.0.1:5000 (Flask под gunicorn)
```

Почему именно так, а не как было (`http://45.143.93.41:5000/api` прямо из
браузера): страница по https не может ходить на http-адрес — браузер такие
запросы блокирует, и сайт просто перестанет показывать товары. А сертификат
на голый IP с портом не выпишешь.

Адрес API нигде не прописан: `frontend/js/api.js` берёт тот же домен, что и
страница. Если API однажды переедет на отдельный адрес — он задаётся в
`frontend/js/config.js` полем `apiBase` и обязан быть по https.

## Первая установка

Всё выполняется от root на VPS. Путь `/var/www/collablab` можно заменить
своим — тогда поменяйте его и в конфигах ниже.

### 1. Код и зависимости

```bash
apt update && apt install -y nginx python3-pip python3-venv git
git clone https://github.com/zabatv/collablab.git /var/www/collablab
cd /var/www/collablab
git checkout claude/exciting-thompson-zfc484

python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

Если папка уже есть — вместо clone: `cd /var/www/collablab && git pull`.

### 2. Flask как служба

Пароль админки задаётся здесь и нигде больше — в репозитории его нет и быть
не должно. Придумайте длинный.

```bash
cat > /etc/systemd/system/shop-api.service <<'UNIT'
[Unit]
Description=ROBOT catalogue API
After=network.target

[Service]
WorkingDirectory=/var/www/collablab/backend
Environment=ADMIN_USERNAME=admin
Environment=ADMIN_PASSWORD=ЗАМЕНИТЕ_НА_СВОЙ_ПАРОЛЬ
ExecStart=/var/www/collablab/.venv/bin/gunicorn --preload -w 3 \
          -b 127.0.0.1:5000 --timeout 300 app:app
Restart=always
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
UNIT

chown -R www-data:www-data /var/www/collablab/backend/data /var/www/collablab/frontend/uploads
chmod 600 /etc/systemd/system/shop-api.service   # в файле пароль
systemctl daemon-reload
systemctl enable --now shop-api
systemctl status shop-api --no-pager
```

`--preload` обязателен: без него каждый рабочий процесс отдельно полезет
обновлять схему базы и они столкнутся. `--timeout 300` — на экспорт Excel с
картинками и заливку видео.

### 3. nginx

```bash
cat > /etc/nginx/sites-available/robots07.com <<'CONF'
server {
    listen 80;
    listen [::]:80;
    server_name robots07.com www.robots07.com;

    root /var/www/collablab/frontend;
    index index.html;

    # Адреса без .html: /catalog открывает catalog.html
    location / {
        try_files $uri $uri.html $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:5000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Видео товара весит до 200 МБ, и заливка идёт не быстро
        client_max_body_size 300m;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_request_buffering off;
    }

    location /uploads/ {
        expires 30d;
        add_header Cache-Control "public";
        access_log off;
    }

    # Страницы меняются с каждым обновлением сайта
    location ~* \.(html|js|css)$ {
        expires 10m;
    }
}
CONF

ln -sf /etc/nginx/sites-available/robots07.com /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

Проверка до сертификата: `curl -I http://robots07.com/` и
`curl http://robots07.com/api/categories | head -c 200`.

### 4. HTTPS

A-записи `robots07.com` и `www.robots07.com` должны уже указывать на IP
VPS (проверить: `dig +short robots07.com`).

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d robots07.com -d www.robots07.com
```

Certbot сам добавит 443-й блок и перенаправление с http. Обновление
сертификата дальше идёт по таймеру, проверить: `systemctl list-timers | grep certbot`.

### 5. Файрвол

```bash
ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw --force enable
```

Порт 5000 наружу не открывается: Flask слушает `127.0.0.1`, снаружи к нему
попадают только через nginx.

## Обновление сайта

```bash
cd /var/www/collablab && git pull
systemctl restart shop-api      # нужно, только если менялся backend/
```

Статика подхватывается сразу. Если браузер показывает старое — у скриптов в
HTML есть версия (`?v=20261007`), она меняется вместе с кодом; своё
кэширование можно сбросить Ctrl+F5.

## База и её копии

База — один файл `backend/data/products.db`. Копия делается копированием
файла, лучше средствами sqlite3, чтобы не поймать его в середине записи:

```bash
apt install -y sqlite3
mkdir -p /var/backups/shop
sqlite3 /var/www/collablab/backend/data/products.db \
  ".backup '/var/backups/shop/products-$(date +%F).db'"
```

В `cron` ежедневно:

```bash
echo '0 3 * * * root sqlite3 /var/www/collablab/backend/data/products.db ".backup \
  /var/backups/shop/products-$(date +\%F).db" && find /var/backups/shop -mtime +14 -delete' \
  > /etc/cron.d/shop-backup
```

Фотографии и видео лежат в `frontend/uploads/` — их тоже стоит копировать
(`rsync -a frontend/uploads/ /var/backups/shop/uploads/`).

## Вариант с их бэкендом: сайт на VPS, каталог на Windows

Каталог живёт на Windows-компьютере рядом с 1С, у которого нет белого IP.
VPS принимает HTTPS и передаёт запросы по WireGuard-туннелю:

```
браузер ─https─> nginx на VPS ─┬─ /         → frontend/ (страницы сайта)
                               ├─ /api/     → 10.8.0.2:3000 через туннель
                               └─ /api/media/ → то же, с кэшем на VPS
```

Их собственная инструкция отдаёт API прямо на корне домена. Нам корень нужен
под страницы, поэтому API уходит на `/api/`, а слэш в конце `proxy_pass`
срезает этот префикс: `/api/products` приходит к ним как `/products`.

Адресация туннеля из их документации: VPS `10.8.0.1`, Windows `10.8.0.2`,
UDP 51820. Порт 3000 на Windows открыт только для 10.8.0.1.

### Конфиг nginx

`/etc/nginx/conf.d/shop-cache.conf`:

```nginx
proxy_cache_path /var/cache/nginx/shop_media levels=1:2 keys_zone=shop_media:10m
                 max_size=2g inactive=7d use_temp_path=off;
```

`/etc/nginx/sites-available/robots07.com`:

```nginx
# Каталог на Windows-компьютере, доступен только через WireGuard
upstream shop_backend {
    server 10.8.0.2:3000;
    keepalive 16;
}

server {
    listen 80;
    listen [::]:80;
    server_name robots07.com www.robots07.com;

    root /var/www/collablab/frontend;
    index index.html;

    # Страницы сайта: /catalog открывает catalog.html
    location / {
        try_files $uri $uri.html $uri/ /index.html;
    }

    # Фото и видео: через туннель тянутся только первый раз
    location /api/media/ {
        proxy_pass http://shop_backend/media/;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_cache shop_media;
        proxy_cache_valid 200 1h;
        proxy_cache_use_stale error timeout updating;
        add_header X-Cache $upstream_cache_status;
        expires 1d;
    }

    location /api/ {
        proxy_pass http://shop_backend/;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }

    # Компьютер выключен или туннель упал
    error_page 502 503 504 /maintenance.html;
    location = /maintenance.html {
        root /var/www/shop;
        internal;
    }
}
```

### Настройка сайта

`frontend/js/config.js`:

```js
backend: 'node',
node: {
  public: 'https://robots07.com/api',
  admin:  'http://127.0.0.1:3001',
},
```

Сайт и API оказываются на одном домене, поэтому CORS витрине вообще не нужен:
браузер считает такие запросы своими.

### Сервис сайта: бренды, слайдер, описания разделов, счётчики, SEO

Их бэкенд знает только каталог: товары, цены, остатки, фотографии. Брендов,
слайдера на главной, текста под заголовком раздела, статистики просмотров и
разбора готовности каталога у него нет и не будет — из 1С такие вещи не
приходят. Всё это держит наш
маленький сервис на этом же VPS, а правит заказчик в админке, как раньше.

Разбору каталога нужны описания товаров, поэтому сервис сам ходит за ними в
их API внутри туннеля (`CATALOG_API`) и держит ответ десять минут.

```bash
cd /var/www/collablab
python3 -m venv .venv
.venv/bin/pip install -r services/site/requirements.txt

cp docs/systemd/shop-site.service /etc/systemd/system/
sed -i "s/ЗАМЕНИТЕ_ПАРОЛЬ/ТОТ_ЖЕ_ПАРОЛЬ_ЧТО_В_ENV_НА_WINDOWS/" \
       /etc/systemd/system/shop-site.service

chown -R www-data:www-data /var/www/collablab/services/site
chmod 600 /etc/systemd/system/shop-site.service   # в файле пароль
systemctl daemon-reload
systemctl enable --now shop-site
curl -s http://127.0.0.1:5001/health
```

Пароль обязан совпадать с `ADMIN_PASSWORD` на Windows: тогда в админке один
вход на оба сервера. Без пароля сервис поднимется, но запись будет закрыта.

Данные сервиса — `services/site/data/brands.db`, а рядом логотипы
(`logos/`), слайды (`banners/`) и документация к товарам (`docs/`). Это
единственное, что на VPS стоит копировать: каталог живёт на Windows.

### Админка

Админский сервер слушает `127.0.0.1` и через туннель не проходит — так у них
задумано. Поэтому страница админки работает **с самого Windows-компьютера**:
открыть на нём `https://robots07.com/admin`, а запросы пойдут на
`http://127.0.0.1:3001`. Браузер это разрешает, `127.0.0.1` для него
доверенный адрес, а домен вписан в `ADMIN_ALLOWED_ORIGINS`.

Ничего дополнительно открывать наружу не надо. Если понадобится править
каталог не с той машины, это отдельный шаг: `ADMIN_HOST=10.8.0.2`, правило
файрвола для порта 3001 с 10.8.0.1, свой `location` в nginx и обязательно
длинный `ADMIN_PASSWORD` — админка окажется в интернете за одним лишь
паролем, так что решать это стоит осознанно.
