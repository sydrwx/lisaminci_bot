"""
parsers/wiki_xml.py — Парсинг XML дампа вики Genshin Impact и загрузка в ChromaDB

Использование:
    pip install chromadb mwparserfromhell python-dotenv
    python -m parsers.wiki_xml --xml data/Genshin+Impact+Вики-20260512121848.xml

Результат: локальная ChromaDB в папке ./chroma_db
"""

import os
import re
import argparse
import xml.etree.ElementTree as ET

from common.db import CHROMA_DIR, COLLECTION, get_client, get_collection

# ─── Конфигурация ────────────────────────────────────────────────────────────
BATCH_SIZE  = 100
MIN_LENGTH  = 100
MW_NS       = "http://www.mediawiki.org/xml/export-0.11/"
CHUNK_SIZE  = 500  # символов на чанк
# ─────────────────────────────────────────────────────────────────────────────


# Параметры инфобоксов, которые не несут полезного текста
SKIP_PARAM = re.compile(r"изображ|image|голос|галере|gallery|файл|иконк|icon|цвет|color|звук|видео", re.I)


def render_templates(code) -> None:
    """Заменить шаблоны с именованными параметрами (инфобоксы) строками «ключ: значение».

    strip_code() выбрасывает шаблоны целиком, а в инфобоксах — редкость, элемент,
    оружие, регион, дата выхода, баннер и т.п.
    """
    import mwparserfromhell
    for tpl in code.filter_templates(recursive=False):
        named = [p for p in tpl.params if p.showkey]
        if len(named) < 3:
            continue
        lines = []
        for p in named:
            key = str(p.name).strip()
            if SKIP_PARAM.search(key):
                continue
            value = mwparserfromhell.parse(str(p.value)).strip_code().strip()
            value = re.sub(r"\s+", " ", value)
            if value:
                lines.append(f"{key.replace('_', ' ')}: {value}")
        try:
            code.replace(tpl, "\n" + "\n".join(lines) + "\n\n" if lines else "")
        except ValueError:
            pass


def clean_wikitext(text: str) -> str:
    """Убрать вики-разметку и оставить чистый текст (данные инфобоксов сохраняются)."""
    try:
        import mwparserfromhell
        code = mwparserfromhell.parse(text)
        for ref in code.filter_tags(matches=lambda n: str(n.tag).lower() == "ref"):
            try:
                code.remove(ref)
            except ValueError:
                pass
        render_templates(code)
        text = code.strip_code()
    except ImportError:
        text = re.sub(r'\{\{[^}]*\}\}', '', text)
        text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', text)
        text = re.sub(r'\[https?://\S+ ([^\]]+)\]', r'\1', text)
        text = re.sub(r"'{2,3}", '', text)
        text = re.sub(r'==+\s*(.*?)\s*==+', r'\1', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'&lt;|&gt;|&amp;|&quot;', ' ', text)

    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def parse_xml(xml_path: str) -> list[dict]:
    """Распарсить XML дамп и вернуть список статей."""
    print(f"📖 Читаю XML: {xml_path}")
    ns = {"mw": MW_NS}

    tree = ET.parse(xml_path)
    root = tree.getroot()
    pages = root.findall("mw:page", ns)
    print(f"   Всего страниц в XML: {len(pages)}")

    articles = []
    skipped  = 0

    for page in pages:
        page_ns = page.find("mw:ns", ns)
        if page_ns is None or page_ns.text != "0":
            continue

        title = page.find("mw:title", ns)
        if title is None:
            continue

        revision = page.find("mw:revision", ns)
        if revision is None:
            continue

        text_el = revision.find("mw:text", ns)
        if text_el is None or not text_el.text:
            continue

        raw_text = text_el.text

        if raw_text.strip().lower().startswith("#redirect") or \
           raw_text.strip().lower().startswith("#перенаправление"):
            skipped += 1
            continue

        clean = clean_wikitext(raw_text)
        if len(clean) < MIN_LENGTH:
            skipped += 1
            continue

        articles.append({"title": title.text, "text": clean})

    print(f"   Полезных статей: {len(articles)} (пропущено: {skipped})")
    return articles


def chunk_article(title: str, text: str) -> list[dict]:
    """Разбить статью на чанки, каждый сохраняет заголовок как контекст."""
    chunks = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    current = f"# {title}\n"
    for para in paragraphs:
        if len(current) + len(para) > CHUNK_SIZE and len(current) > len(f"# {title}\n"):
            chunks.append(current.strip())
            current = f"# {title}\n{para}\n"
        else:
            current += para + "\n"

    if current.strip() and current.strip() != f"# {title}":
        chunks.append(current.strip())

    return [{"title": title, "text": c, "chunk_id": i} for i, c in enumerate(chunks)]


def load_to_chroma(articles: list[dict]):
    """Загрузить статьи в ChromaDB с дефолтными ONNX embeddings."""
    print(f"\n🗄️  Инициализирую ChromaDB ({CHROMA_DIR})...")
    client = get_client()

    try:
        client.delete_collection(COLLECTION)
        print("   Старая коллекция удалена")
    except Exception:
        pass

    collection = get_collection(client, create=True)

    print("✂️  Разбиваю статьи на чанки...")
    all_chunks = []
    for art in articles:
        all_chunks.extend(chunk_article(art["title"], art["text"]))
    print(f"   Всего чанков: {len(all_chunks)}")

    print(f"📤 Загружаю в ChromaDB (батчи по {BATCH_SIZE})...")
    total = (len(all_chunks) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(all_chunks), BATCH_SIZE):
        batch     = all_chunks[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1

        ids       = [f"{c['title']}__chunk{c['chunk_id']}" for c in batch]
        documents = [c["text"] for c in batch]
        metadatas = [{"title": c["title"], "chunk_id": c["chunk_id"]} for c in batch]

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        print(f"   [{batch_num}/{total}] загружено {i + len(batch)}/{len(all_chunks)} чанков")

    print(f"\n✅ Готово: {len(all_chunks)} чанков из {len(articles)} статей")
    print(f"   ChromaDB: {CHROMA_DIR}/")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--xml", required=True, help="Путь к XML файлу дампа вики")
    args = parser.parse_args()

    articles = parse_xml(args.xml)
    load_to_chroma(articles)
    print("\n🎉 База знаний готова! Теперь запускай python -m bot")


if __name__ == "__main__":
    main()
