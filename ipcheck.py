"""
ipcheck.py — проверка, что внешний IP принадлежит разрешённой подсети

Проверяется IP, с которым запросы выходят через ANTHROPIC_PROXY (прокси Claude API);
без прокси — IP прямого подключения.

Используется:
    - как отдельный сервис ipcheck в docker-compose: запускается первым, при неверном
      IP падает (exit 1), и бот не стартует;
    - внутри bot.py: периодически; при неверном IP бот не падает, а перестаёт
      обращаться к Claude («Акаша недоступен»);
    - в parser.py: перед стартом.

Внешний IP определяется через публичные сервисы (не через Claude API).
Fail-closed: если IP не удалось определить — проверка не пройдена.

    python ipcheck.py     # exit 0 — IP в подсети, exit 1 — нет
"""

import os
import sys
import logging
import ipaddress
import urllib.request

# Обязательная переменная: без неё проверка не проходит (fail-closed)
ALLOWED_SUBNET  = ipaddress.ip_network(os.environ["ALLOWED_SUBNET"]) if os.getenv("ALLOWED_SUBNET") else None
ANTHROPIC_PROXY = os.getenv("ANTHROPIC_PROXY") or None
IP_SERVICES = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
    "https://ipinfo.io/ip",
]
TIMEOUT = 10

log = logging.getLogger("ipcheck")


class IPCheckError(RuntimeError):
    pass


def fetch_ip(opener, url: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with opener.open(req, timeout=TIMEOUT) as r:
        return ipaddress.ip_address(r.read().decode().strip())


def check_ip(proxy: str | None = ANTHROPIC_PROXY) -> str:
    """Вернуть внешний IP (через proxy, если задан), если он в ALLOWED_SUBNET, иначе IPCheckError.

    Опрашиваются все сервисы: если хоть один видит IP вне подсети — отказ.
    """
    if ALLOWED_SUBNET is None:
        raise IPCheckError("ALLOWED_SUBNET не задан")
    # Явный ProxyHandler: без proxy — строго напрямую, игнорируя HTTP(S)_PROXY окружения
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    )
    via = f"через прокси {proxy}" if proxy else "напрямую"
    seen = {}
    for url in IP_SERVICES:
        try:
            seen[url] = fetch_ip(opener, url)
        except Exception as e:
            log.warning(f"{url}: не удалось получить IP ({e})")

    if not seen:
        raise IPCheckError(f"Не удалось определить внешний IP ({via}) ни через один сервис")

    bad = {u: str(ip) for u, ip in seen.items() if ip not in ALLOWED_SUBNET}
    if bad:
        raise IPCheckError(f"Внешний IP ({via}) вне подсети {ALLOWED_SUBNET}: {bad}")

    ips = sorted({str(ip) for ip in seen.values()})
    log.info(f"Внешний IP ({via}) {', '.join(ips)} в подсети {ALLOWED_SUBNET}")
    return ips[0]


def main() -> int:
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
    try:
        check_ip()
        return 0
    except IPCheckError as e:
        log.error(f"{e}. Бот с Claude API запускать нельзя.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
