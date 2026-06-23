# handlers/xui/links.py

from urllib.parse import quote, urlparse
import re
from collections.abc import Iterable

from handlers.xui.api.client import xui_get
from handlers.xui.api.helpers import parse_stream_settings


def get_server_host() -> str:
    from handlers.xui.config_runtime import get_xui_url
    return urlparse(get_xui_url() or "").hostname


def _walk_strings(value) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_walk_strings(item))
        return found
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            found.extend(_walk_strings(item))
        return found
    return []


def _find_first_key(value, keys: tuple[str, ...]):
    if isinstance(value, dict):
        for key in keys:
            if key in value and value.get(key) not in (None, ""):
                return value.get(key)
        for item in value.values():
            found = _find_first_key(item, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_key(item, keys)
            if found not in (None, ""):
                return found
    return None


def _find_setting_value(value, *, want_port: bool = False, want_path: bool = False):
    if isinstance(value, dict):
        for key, inner in value.items():
            key_l = str(key).lower()
            if want_port and "sub" in key_l and "port" in key_l and inner not in (None, ""):
                return inner
            if want_path and "sub" in key_l and ("path" in key_l or "url" in key_l) and inner not in (None, ""):
                return inner
            found = _find_setting_value(inner, want_port=want_port, want_path=want_path)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_setting_value(item, want_port=want_port, want_path=want_path)
            if found not in (None, ""):
                return found
    return None


async def _get_subscription_base() -> str | None:
    from handlers.xui.config_runtime import get_xui_url

    result = await xui_get("/panel/setting/all")
    if not result or not result.get("success"):
        return None

    obj = result.get("obj")
    if obj is None:
        return None

    xui_url = get_xui_url() or ""
    parsed = urlparse(xui_url)
    scheme = parsed.scheme or "https"
    host = parsed.hostname or get_server_host() or ""

    port_value = _find_setting_value(obj, want_port=True)
    path_value = _find_setting_value(obj, want_path=True)

    if isinstance(path_value, str):
        path = path_value.strip()
    else:
        path = "/sub/"

    if path and not path.startswith("/"):
        path = f"/{path}"

    if not host:
        return None

    if port_value:
        try:
            port = int(str(port_value).strip())
        except Exception:
            port = None
    else:
        port = None

    if port:
        return f"{scheme}://{host}:{port}{path}"
    return f"{scheme}://{host}{path}"


async def fetch_subscription_link(email: str, sub_id: str = "") -> str | None:
    email = str(email or "").strip()
    sub_id = str(sub_id or "").strip()
    if not email and not sub_id:
        return None

    def _extract_urls(text: str) -> list[str]:
        return re.findall(r'https?://[^\s"<>\']+', text)

    def _walk(value) -> Iterable[str]:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            found = [text]
            found.extend(_extract_urls(text))
            return found
        if isinstance(value, dict):
            found: list[str] = []
            for key in (
                "subscriptionUrl", "subscriptionURL", "subUrl", "subURL",
                "subLink", "url", "link", "sub", "subscription", "value",
                "data", "obj", "result",
            ):
                if key in value:
                    found.extend(_walk(value.get(key)))
            for item in value.values():
                found.extend(_walk(item))
            return found
        if isinstance(value, list):
            found: list[str] = []
            for item in value:
                found.extend(_walk(item))
            return found
        return []

    def _score_url(url: str) -> tuple[int, int, str]:
        u = str(url or "").strip()
        if not u:
            return (0, 0, "")
        low = u.lower()
        is_http = 1 if low.startswith(("http://", "https://")) else 0
        is_sub = 1 if ("/sub/" in low or "subscription" in low or "subid" in low) else 0
        is_vless = 1 if low.startswith("vless://") else 0
        # Самый высокий приоритет у обычных subscription URL, потом другие http(s), потом всё остальное.
        if is_sub and is_http:
            return (4, 1, u)
        if is_http:
            return (3, is_sub, u)
        if low.startswith("subscription://"):
            return (2, 1, u)
        if not is_vless:
            return (1, 0, u)
        return (0, 0, u)

    results = []
    if sub_id:
        results.append(await xui_get(f"/panel/api/clients/subLinks/{quote(sub_id, safe='')}"))
    if email:
        results.append(await xui_get(f"/panel/api/clients/links/{quote(email, safe='')}"))

    candidates: list[str] = []
    for result in results:
        if not result or not result.get("success"):
            continue
        candidates.extend(_walk(result.get("obj")))

    if not candidates:
        return None

    normalized: list[str] = []
    for c in candidates:
        c = str(c).strip()
        if c and c not in normalized:
            normalized.append(c)

    normalized.sort(key=_score_url, reverse=True)
    best = normalized[0].strip()
    if best.lower().startswith("vless://"):
        # Если панель вернула только vless, пробуем собрать именно subscription URL из настроек.
        base = await _get_subscription_base()
        if base and sub_id:
            return f"{base.rstrip('/')}/{quote(sub_id, safe='')}"
    return best


def build_vless_link(inbound: dict, client_uuid: str, email: str, client_flow: str = "") -> str | None:
    # Старый формат оставлен для совместимости с кодом, который ещё может его использовать.
    if inbound.get("protocol", "").lower() != "vless":
        return None

    host = get_server_host()
    port = inbound.get("port", "")

    stream = parse_stream_settings(inbound)

    network = stream.get("network", "tcp")
    security = stream.get("security", "none")
    params = [f"type={network}", "encryption=none", f"security={security}"]

    if client_flow:
        params.append(f"flow={client_flow}")

    if security == "reality":
        rs = stream.get("realitySettings", {})
        s = rs.get("settings", {})
        pub_key = s.get("publicKey", "")
        fp = s.get("fingerprint", "chrome")
        spx = s.get("spiderX", "/")
        sni = (rs.get("serverNames") or [""])[0]
        sid = (rs.get("shortIds") or [""])[0]
        if pub_key:
            params.append(f"pbk={quote(pub_key, safe='')}")
        if fp:
            params.append(f"fp={fp}")
        if sni:
            params.append(f"sni={sni}")
        if sid:
            params.append(f"sid={sid}")
        params.append(f"spx={quote(spx or '/', safe='')}")

    elif security == "tls":
        ts = stream.get("tlsSettings", {})
        sni = ts.get("serverName", "")
        if sni:
            params.append(f"sni={sni}")
        fp = ts.get("settings", {}).get("fingerprint", "")
        if fp:
            params.append(f"fp={fp}")

    if network == "ws":
        ws = stream.get("wsSettings", {})
        path = ws.get("path", "/")
        params.append(f"path={quote(path)}")
        host_header = ws.get("headers", {}).get("Host", "")
        if host_header:
            params.append(f"host={host_header}")
    elif network == "grpc":
        grpc = stream.get("grpcSettings", {})
        service = grpc.get("serviceName", "")
        if service:
            params.append(f"serviceName={quote(service)}")
        params.append("mode=gun")

    query = "&".join(params)
    fragment = quote(f"🇩🇪 VLESS-{email}", safe='')
    return f"vless://{client_uuid}@{host}:{port}/?{query}#{fragment}"


def build_clash_yaml(inbound: dict, client_uuid: str, email: str, client_flow: str = "") -> str:
    """Генерирует YAML конфиг для Clash Verge"""
    host = get_server_host()
    port = inbound.get("port", "")

    stream = parse_stream_settings(inbound)

    network = stream.get("network", "tcp")
    rs = stream.get("realitySettings", {})
    s = rs.get("settings", {})
    pub_key = s.get("publicKey", "")
    fp = s.get("fingerprint", "chrome")
    sni = (rs.get("serverNames") or [""])[0]
    sid = (rs.get("shortIds") or [""])[0]

    flow_line = f"  flow: {client_flow}\n" if client_flow else ""

    yaml_content = f"""mode: global
mixed-port: 7897
allow-lan: false
log-level: info
ipv6: true
external-controller: ''
secret: set-your-secret
unified-delay: true
external-controller-cors:
  allow-private-network: true
  allow-origins:
  - tauri://localhost
  - http://tauri.localhost
  - https://yacd.metacubex.one
  - https://metacubex.github.io
  - https://board.zash.run.place
profile:
  store-selected: true
external-controller-unix: /tmp/verge/verge-mihomo.sock
tun:
  enable: false
  stack: gvisor
  auto-route: true
  auto-detect-interface: true
  strict-route: false
  dns-hijack:
  - any:53
proxies:
- name: VLESS 🇩🇪
  type: vless
  server: {host}
  port: {port}
  uuid: {client_uuid}
{flow_line}  tls: true
  network: {network}
  reality-opts:
    public-key: {pub_key}
    short-id: {sid}
  client-fingerprint: {fp}
  servername: {sni}
proxy-groups:
- name: Proxy
  type: select
  proxies:
  - VLESS 🇩🇪
rules:
- MATCH,Proxy
"""
    return yaml_content


def build_instruction_text(vless_link: str, device_name: str = "") -> str:
    """Текст инструкции для VPN (Karing). device_name = имя клиента (email)."""
    import html as _h
    note_code = f"<code>{_h.escape(device_name)}</code>" if device_name else "<code>VPN</code>"
    note_plain = _h.escape(device_name) if device_name else "VPN"

    return (
        "🔐 <b>Инструкция по настройке VPN</b>\n\n"

        "📥 <b>Установите Karing:</b>\n"
        "<a href=\"https://github.com/KaringX/karing/releases/download/v1.2.18.2102/karing_1.2.18.2102_android_arm.apk\">Android</a> · "
        "<a href=\"https://apps.apple.com/au/app/karing/id6472431552\">iPhone</a> · "
        "<a href=\"https://github.com/KaringX/karing/releases/download/v1.2.18.2102/karing_1.2.18.2102_windows_x64.exe\">Windows</a> · "
        "<a href=\"https://apps.apple.com/au/app/karing/id6472431552\">MacOS</a>\n\n"

        "⚙️ <b>Настройка:</b>\n"
        "1. Откройте приложение → выберите язык <b>Русский</b> → на каждом следующем шаге нажимайте «Далее»\n"
        "2. Скопируйте ссылку на подписку ниже → в приложении нажмите «Добавить профиль» → «Импорт из буфера обмена»\n"
        f"   В поле «Примечание» вставьте: {note_code} ← нажмите чтобы скопировать\n\n"

        "🇷🇺 <b>Чтобы российские сайты работали с включённым VPN:</b>\n"
        "3. Нажмите ⚙️ (шестерёнка) → <b>Правила перенаправления</b> → ✏️ карандаш → ⋯ три точки → <b>Добавить</b>\n"
        "4. В поле «Примечание» введите <code>RU</code>\n"
        "5. В <b>Rule Set (build-in)</b> добавьте:\n"
        "   <code>geosite:ru</code>\n"
        "   <code>geoip:ru</code>\n"
        "6. В поле «Суффикс доменного имени» введите <code>ru</code>\n"
        "7. Нажмите ✓ (галочка сверху справа) → перетяните <b>RU</b> на самый верх списка личных правил\n"
        "8. Нажмите на <b>RU</b> → выберите <b>Напрямую</b>\n\n"

        "▶️ <b>Запуск:</b>\n"
        "9. На главном меню выберите режим <b>Правила</b> → в самом низу выберите ваш VPN "
        f"<b>{note_plain}</b> → запустите VPN\n\n"

        f"🔗 <b>Ваша ссылка на подписку</b> (нажмите чтобы скопировать):\n<code>{vless_link}</code>\n\n"
        "📖 <a href=\"https://teletype.in/@pratokwau/vpnins\">Подробная инструкция с картинками</a>\n\n""📲 Управлять вашим VPN вы можете в боте @drebolwork_bot → /myvpn"
    )
