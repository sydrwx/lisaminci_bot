"""
src_tg.py — база знаний по настольной игре «Осквернённый Грааль» (Tainted Grail)

Отдельная коллекция ChromaDB (TG_COLLECTION). Источники:
    pdf    — правила «Падение Авалона» и «Короли краха» (русские PDF в TG_PDF_DIR);
    guide  — «Путеводитель по миру „Осквернённого Грааля“» от Crowd Games (VK, рус.);
    lore   — лор вселенной: категория Lore вики Tainted Grail: Conquest (Fandom, англ.;
             компьютерная игра в том же мире, общий лор Авалона).
Энкаунтеры (журнал исследований) намеренно не загружаются.
"""

import os
import re
import glob
import html
import logging

import requests

from xml_parser import clean_wikitext

TG_PDF_DIR = os.getenv("TG_PDF_DIR", "./tainted_grail")
GUIDE_URL = "https://m.vk.ru/@crowdgames-putevoditel-po-miru-oskvernennogo-graalya"
LORE_API = "https://taintedgrail.fandom.com/api.php"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) LisaGenshinBot-updater/1.0"
CHUNK_SIZE = 700
MIN_CYRILLIC = 0.6  # страницы PDF с меньшей долей кириллицы — карты/листы на английском, пропускаем

# Название игры по имени PDF
PDF_GAMES = {
    "fall_of_avalon": "Падение Авалона",
    "koroli": "Короли краха",
    "kings": "Короли краха",
}

log = logging.getLogger("updater.tg")


def chunks(title: str, text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Нарезать текст по абзацам; каждый кусок начинается с заголовка."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|(?<=[.!?…»])\n(?=[А-ЯA-Z«])", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{p}" if cur else p
    if cur:
        out.append(cur)
    return [f"# {title}\n{c}" for c in out]


def fix_pdf_text(t: str) -> str:
    t = re.sub(r"(\w)[‑-]\s*\n\s*(\w)", r"\1\2", t)        # переносы слов
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    # склеиваем строки, разорванные вёрсткой посреди предложения
    t = re.sub(r"(?<=[а-яёa-z,;—])\n(?=[а-яёa-z])", " ", t)
    return t.strip()


def pdf_docs() -> list[dict]:
    from pypdf import PdfReader
    docs = []
    for path in sorted(glob.glob(os.path.join(TG_PDF_DIR, "*.pdf"))):
        base = os.path.basename(path).lower()
        game = next((g for k, g in PDF_GAMES.items() if k in base), os.path.splitext(os.path.basename(path))[0])
        kept = 0
        for n, page in enumerate(PdfReader(path).pages, 1):
            text = page.extract_text() or ""
            letters = re.findall(r"[a-zа-яё]", text, re.I)
            cyr = sum(1 for ch in letters if re.match(r"[а-яё]", ch, re.I))
            if len(text.strip()) < 200 or not letters or cyr / len(letters) < MIN_CYRILLIC:
                continue
            kept += 1
            title = f"«{game}» — правила, стр. {n}"
            for i, c in enumerate(chunks(title, fix_pdf_text(text))):
                docs.append({"id": f"tg__pdf__{base}__p{n}__{i}", "title": f"Правила: {game}",
                             "text": c, "game": game})
        log.info(f"TG: {os.path.basename(path)} → «{game}», страниц с текстом {kept}")
    return docs


def guide_docs() -> list[dict]:
    r = requests.get(GUIDE_URL, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", r.text, flags=re.S)
    t = re.sub(r"<br\s*/?>|</p>|</h\d>|</li>", "\n\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", t)
    start = t.find("«Осквернённый Грааль»")
    if start < 0:
        raise RuntimeError("путеводитель: не найден текст статьи")
    t = t[start:]
    # обрезаем хвост страницы VK (комментарии, меню)
    for marker in ("Нравится", "Поделиться", "Показать комментарии"):
        i = t.find(marker)
        if i > 1000:
            t = t[:i]
    # склеиваем строки, разорванные ссылками/выделением внутри предложения
    t = re.sub(r"\s*\n\s*(?=[а-яёa-z,.;:»)—])", " ", t)
    title = "Путеводитель по миру «Осквернённого Грааля» (Crowd Games)"
    return [{"id": f"tg__guide__{i}", "title": title, "text": c, "game": "общее"}
            for i, c in enumerate(chunks(title, t.strip()))]


def lore_docs() -> list[dict]:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    params = {"action": "query", "generator": "categorymembers", "gcmtitle": "Category:Lore",
              "gcmlimit": "100", "prop": "revisions", "rvprop": "content", "rvslots": "main",
              "format": "json", "formatversion": "2"}
    docs, cont = [], {}
    while True:
        data = s.get(LORE_API, params={**params, **cont}, timeout=60).json()
        for page in data.get("query", {}).get("pages", []):
            revs = page.get("revisions") or []
            if not revs:
                continue
            text = clean_wikitext(revs[0]["slots"]["main"]["content"])
            title = f"Лор вселенной Tainted Grail: {page['title']}"
            for i, c in enumerate(chunks(title, text)):
                docs.append({"id": f"tg__lore__{page['title']}__{i}", "title": title,
                             "text": c, "game": "лор вселенной (англ.)"})
        if "continue" not in data:
            return docs
        cont = data["continue"]


def collect() -> list[dict]:
    docs = []
    for name, fn in (("PDF правил", pdf_docs), ("путеводитель", guide_docs), ("лор", lore_docs)):
        try:
            part = fn()
            docs += part
            log.info(f"TG: {name} — документов {len(part)}")
        except Exception as e:
            # один недоступный источник не должен стирать остальные из базы
            raise RuntimeError(f"TG: источник «{name}» недоступен: {e}") from e
    return docs
