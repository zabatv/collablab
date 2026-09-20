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

## Когда включим их бэкенд

Каталог тогда переезжает на Node + PostgreSQL на Windows-машине с 1С, а VPS
остаётся точкой входа: по их `docs/vps-reverse-proxy.md` это nginx плюс
WireGuard-туннель. На стороне сайта меняется одна строка в
`frontend/js/config.js` — `backend: 'node'` и адреса их серверов.
Подробности в [node-backend.md](node-backend.md).
