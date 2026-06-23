# handlers/xui/links.py

from urllib.parse import quote, urlparse

from handlers.xui.api.client import xui_get
from handlers.xui.api.helpers import parse_stream_settings


def get_server_host() -> str:
    from handlers.xui.config_runtime import get_xui_url
    return urlparse(get_xui_url() or "").hostname


async def fetch_subscription_link(sub_id: str) -> str | None:
    sub_id = str(sub_id or "").strip()
    if not sub_id:
        return None

    result = await xui_get(f"/panel/api/clients/subLinks/{quote(sub_id, safe='')}")
    if not result or not result.get("success"):
        return None

    obj = result.get("obj")
    if isinstance(obj, str) and obj.strip():
        return obj.strip()
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                for key in ("url", "link", "sub", "subscription", "value"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
    if isinstance(obj, dict):
        for key in ("url", "link", "sub", "subscription", "value"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for val in obj.values():
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


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
