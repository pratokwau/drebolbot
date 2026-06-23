# 3X-UI API Reference (v3.1.0)

Базовый URL: `{PANEL_URL}/panel/api/`

Все эндпоинты возвращают обёртку `{ success, msg, obj }`, если не указано иное.

## Аутентификация

| Режим | Как использовать |
|---|---|
| Session cookie | `POST /login` → cookie для браузера/SPA |
| Bearer token | `Authorization: Bearer <token>` — для ботов и скриптов |

Токены создаются в **Settings → Security → API Token**. Bearer-запросы не требуют CSRF.

```bash
curl -X GET \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Accept: application/json" \
  https://panel.example.com/panel/api/inbounds/list
```

### Authentication (`/login`, `/logout`, …)

| Метод | Путь | Описание |
|---|---|---|
| POST | `/login` | Логин (username, password, twoFactorCode?) → session cookie |
| POST | `/logout` | Выход, сброс cookie |
| GET | `/csrf-token` | CSRF-токен для SPA (Bearer не нужен) |
| POST | `/getTwoFactorEnable` | Проверка включён ли 2FA |

---

## Inbounds — `/panel/api/inbounds` (13 endpoints)

Управление инбаундами и их клиентами.

| Метод | Путь | Описание |
|---|---|---|
| GET | `/list` | Все инбаунды + clientStats. settings/streamSettings/sniffing — JSON-объекты |
| GET | `/list/slim` | Как `/list`, но clients[] урезаны до `{email, enable, comment}` |
| GET | `/options` | Лёгкий список для dropdown: id, remark, protocol, port, tlsFlowCapable |
| GET | `/get/:id` | Один инбаунд по ID |
| POST | `/add` | Создать инбаунд (полный payload) |
| POST | `/del/:id` | Удалить инбаунд + client stats |
| POST | `/update/:id` | Полная замена конфига (тяжело на больших инбаундах) |
| POST | `/setEnable/:id` | Только toggle enable `{ "enable": bool }` |
| POST | `/:id/resetTraffic` | Обнулить upload+download инбаунда |
| POST | `/resetAllTraffics` | Обнулить трафик всех инбаундов |
| POST | `/import` | Импорт из JSON (form: `data`) |
| GET | `/:id/fallbacks` | Список fallback-правил VLESS/Trojan |
| POST | `/:id/fallbacks` | Заменить fallback-список (рестарт Xray) |

**Используется в боте:** `GET /list` — главное меню `/xui`, статистика клиентов.

---

## Clients — `/panel/api/clients` (20 endpoints)

Клиенты как отдельные сущности, привязываются к одному или нескольким инбаундам.

| Метод | Путь | Описание |
|---|---|---|
| GET | `/list` | Все клиенты + inboundIds + traffic |
| GET | `/list/paged` | Пагинация (page, pageSize≤200, search, filter, sort) |
| GET | `/get/:email` | Клиент по email + inboundIds |
| POST | `/add` | Создать клиента `{ client, inboundIds[] }` |
| POST | `/update/:email` | Полная замена полей клиента |
| POST | `/del/:email` | Удалить (`?keepTraffic=1` — сохранить traffic row) |
| POST | `/:email/attach` | Привязать к инбаундам `{ inboundIds[] }` |
| POST | `/:email/detach` | Отвязать от инбаундов |
| POST | `/resetAllTraffics` | Сброс трафика всех клиентов |
| POST | `/delDepleted` | Удалить исчерпавших/просроченных |
| POST | `/bulkAdjust` | Массовое изменение expiry/traffic `{ emails[], addDays, addBytes }` |
| POST | `/resetTraffic/:email` | Сброс трафика одного клиента |
| POST | `/updateTraffic/:email` | Ручная корректировка `{ upload, download }` |
| POST | `/ips/:email` | Список IP подключений |
| POST | `/clearIps/:email` | Очистить IP-лист |
| POST | `/onlines` | Email'ы онлайн-клиентов |
| POST | `/lastOnline` | Map email → last-seen timestamp |
| GET | `/traffic/:email` | Счётчики трафика |
| GET | `/subLinks/:subId` | Все URL по subscription ID |
| GET | `/links/:email` | Все URL клиента (как Copy URL в UI) |

**Используется в боте:**

| Функция бота | API |
|---|---|
| Создание устройства | `POST /add` |
| Чтение клиента | `GET /get/:email` + UUID из `/inbounds/list` |
| Вкл/выкл, обновление | `POST /update/:email` |
| Удаление | `POST /del/:email` или `POST /:email/detach` |
| Сброс трафика | `POST /resetTraffic/:email` |
| Синхронизация устройств | `GET /list/paged` |

### Поля клиента (POST /add)

```json
{
  "client": {
    "email": "alice@example.com",
    "totalGB": 53687091200,
    "expiryTime": 1735689600000,
    "tgId": 0,
    "limitIp": 0,
    "enable": true,
    "flow": "xtls-rprx-vision"
  },
  "inboundIds": [3, 5]
}
```

Секреты (UUID, password, auth) генерируются сервером, если не переданы.

---

## Server — `/panel/api/server` (26 endpoints)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/status` | CPU, RAM, disk, netIO, xray.state — кэш 2 сек |
| GET | `/cpuHistory/:bucket` | Legacy CPU history |
| GET | `/history/:metric/:bucket` | Time-series: cpu, mem, netUp, netDown, online, load1/5/15 |
| GET | `/xrayMetricsState` | Состояние Xray metrics |
| GET | `/xrayMetricsHistory/:metric/:bucket` | xrAlloc, xrSys, xrHeapObjects, … |
| GET | `/xrayObservatory` | Latency/health по outbound'ам |
| GET | `/xrayObservatoryHistory/:tag/:bucket` | История observatory |
| GET | `/getXrayVersion` | Доступные версии Xray |
| GET | `/getPanelUpdateInfo` | Проверка обновления панели |
| GET | `/getConfigJson` | Текущий Xray config |
| GET | `/getDb` | Скачать SQLite БД |
| GET | `/getNewUUID` | UUID v4 |
| GET | `/getNewX25519Cert` | Reality keypair |
| GET | `/getNewmldsa65` | Post-quantum ML-DSA-65 |
| GET | `/getNewmlkem768` | Post-quantum ML-KEM-768 |
| GET | `/getNewVlessEnc` | VLESS encryption options |
| POST | `/stopXrayService` | Остановить Xray |
| POST | `/restartXrayService` | Перезапустить Xray |
| POST | `/installXray/:version` | Установить версию (`latest`) |
| POST | `/updatePanel` | Self-update панели |
| POST | `/updateGeofile` | Обновить geo-файлы |
| POST | `/updateGeofile/:fileName` | Один geo-файл |
| POST | `/logs/:count` | Логи панели |
| POST | `/xraylogs/:count` | Логи Xray (filter, showDirect/Blocked/Proxy) |
| POST | `/importDB` | Восстановить БД (multipart) |
| POST | `/getNewEchCert` | ECH keypair для SNI |

**Используется в боте:** `GET /status` — мониторинг VPN в `main.py` (xray.state == "running").

Bucket sizes: `2, 30, 60, 120, 180, 300` секунд.

---

## Nodes — `/panel/api/nodes` (9 endpoints)

Удалённые панели как ноды центральной панели.

| Метод | Путь | Описание |
|---|---|---|
| GET | `/list` | Все ноды + health |
| GET | `/get/:id` | Одна нода |
| POST | `/add` | Добавить ноду |
| POST | `/update/:id` | Обновить |
| POST | `/del/:id` | Удалить |
| POST | `/setEnable/:id` | Вкл/выкл синхронизацию |
| POST | `/test` | Проверить без сохранения |
| POST | `/probe/:id` | Проверить существующую |
| GET | `/history/:id/:metric/:bucket` | Метрики ноды |

---

## Custom Geo — `/panel/api/custom-geo` (7 endpoints)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/list` | Кастомные geo-источники |
| GET | `/aliases` | Доступные алиасы для routing |
| POST | `/add` | Добавить источник |
| POST | `/update/:id` | Обновить |
| POST | `/delete/:id` | Удалить |
| POST | `/download/:id` | Перекачать один |
| POST | `/update-all` | Перекачать все |

---

## Backup — 1 endpoint

| Метод | Путь | Описание |
|---|---|---|
| POST | `/panel/api/backuptotgbot` | Отправить бэкап БД в Telegram админам |

---

## Settings — `/panel/setting` (6 endpoints)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/all` | Все настройки панели |
| GET | `/defaultSettings` | Дефолты по host |
| POST | `/update` | Сохранить все настройки |
| POST | `/updateUser` | Сменить логин/пароль |
| POST | `/restartPanel` | Рестарт панели (~5-10 сек) |
| GET | `/getDefaultJsonConfig` | Дефолтный Xray JSON |

---

## API Tokens — `/panel/setting/apiTokens` (4 endpoints)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/apiTokens` | Список токенов |
| POST | `/apiTokens/create` | Создать `{ name }` |
| POST | `/apiTokens/delete/:id` | Удалить |
| POST | `/apiTokens/setEnabled/:id` | Вкл/выкл `{ enabled }` |

---

## Xray Settings — `/panel/xray` (9 endpoints)

| Метод | Путь | Описание |
|---|---|---|
| POST | `/` | Config template + inbound tags |
| GET | `/getDefaultJsonConfig` | Дефолтный config |
| GET | `/getOutboundsTraffic` | Трафик outbound'ов |
| GET | `/getXrayResult` | stdout/stderr Xray |
| POST | `/update` | Сохранить config template |
| POST | `/warp/:action` | Cloudflare Warp (data, del, config, reg, license) |
| POST | `/nord/:action` | NordVPN (countries, servers, reg, setKey, data, del) |
| POST | `/resetOutboundsTraffic` | Сброс трафика outbound |
| POST | `/testOutbound` | Тест outbound |

---

## Subscription Server (отдельный порт, по умолчанию 10882)

| Путь | Описание |
|---|---|
| `/{subPath}:subid` | Base64 подписка (default: `/sub/:subid`) |
| `/{jsonPath}:subid` | JSON-массив прокси |
| `/{clashPath}:subid` | Clash/Mihomo YAML |

**Response headers:** `Subscription-Userinfo`, `Profile-Title`, `Profile-Update-Interval`, …

---

## WebSocket — `/ws` (5 message types)

Требует session cookie (Bearer не поддерживается).

| type | Описание |
|---|---|
| `status` | Health snapshot каждые 2 сек |
| `xrayState` | running / stopped / error |
| `notification` | Toast (рестарт, импорт БД, …) |
| `invalidate` | Перезагрузить ресурс в UI |

---

## Маппинг: модуль бота → API

```
handlers/xui/api/client.py     → xui_get, xui_post (HTTP-клиент)
handlers/xui/api/inbounds.py   → api_get_inbounds
handlers/xui/api/clients.py    → api_add_client, api_get_client, api_update_client, …
handlers/xui/storage.py        → data/vpn_users.json (локальные привязки TG ↔ клиенты)
handlers/xui/links.py          → build_vless_link (локальная генерация, альтернатива /links/:email)
main.py check_vpn_status       → GET /panel/api/server/status
```
