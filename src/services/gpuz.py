import time
import re

import fitz
import ollama

from .config import load_config

OLLAMA_URL = load_config().ollama_url

# ======== 1 ЭТАП ==========
def findDateInGPZU(GPZU_filename: str) -> str:
    ## ИЩЕМ ДАТУ В ДОКУМЕНТЕ, БОЛЕЕ СЛОЖНЫЙ СЛУЧАЙ С ИСПОЛЬЗОВАНИЕМ визуально-лингвистической модели
    # Сохраняем в issuance_date.txt
    print(" Начинаем поиск даты выдачи...")

    date = _find_date_with_text_search(GPZU_filename)

    if date:
        print(f"\n Итог: Найдена дата выдачи: {date}")
        result = date
    else:
        print("\n  Итог: Дата выдачи не найдена")
        result = "не найдено"
    return result


def _find_date_with_text_search(pdf_path):
    """Текстовый поиск заголовка и даты на следующих страницах."""
    doc = fitz.open(pdf_path)

    print("  Выполняем текстовый поиск...")

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()

        title_pattern = r'ГРАДОСТРОИТЕЛЬНЫЙ\s+ПЛАН\s+ЗЕМЕЛЬНОГО\s+УЧАСТКА'
        title_match = re.search(title_pattern, text, re.IGNORECASE)

        if not title_match:
            continue

        print(f" Найден заголовок ГПЗУ на странице {page_num + 1}")

        full_text = text[title_match.start():]

        for i in range(page_num + 1, min(page_num + 5, len(doc))):
            full_text += " " + doc[i].get_text()

        date_patterns = [
            r'Дата\s+выдачи\s*[:]?\s*(\d{2}\.\d{2}\.\d{4})',
            r'дата\s+выдачи\s*[:]?\s*(\d{2}\.\d{2}\.\d{4})',
            r'ДАТА\s+ВЫДАЧИ\s*[:]?\s*(\d{2}\.\d{2}\.\d{4})',
            r'Выдана\s*[:]?\s*(\d{2}\.\d{2}\.\d{4})',
        ]

        for pattern in date_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                date = match.group(1)
                print(f" Найдена дата выдачи: {date}")

                with open('issuance_date.txt', 'w', encoding='utf-8') as f:
                    f.write(date)

                doc.close()
                return date

        all_dates = re.findall(r'\d{2}\.\d{2}\.\d{4}', full_text)
        if all_dates:
            date = all_dates[0]
            print(f" Найдена дата: {date}")

            with open('issuance_date.txt', 'w', encoding='utf-8') as f:
                f.write(date)

            doc.close()
            return date

        print(f" Заголовок найден, но дата не найдена на следующих страницах")

    print(" Текстовый поиск не дал результатов, переходим к визуальному анализу...")
    return _find_issuance_date(pdf_path)


def _find_issuance_date(pdf_path):
    doc = fitz.open(pdf_path)

    # Сначала находим страницу с заголовком ГПЗУ
    title_page = None
    for page_num in range(len(doc)):
        print(f"\  Ищем заголовок на странице {page_num + 1}...")

        page = doc[page_num]
        pix = page.get_pixmap(dpi=200)
        img_data = pix.tobytes("png")

        prompt = """На этой странице есть текст «ГРАДОСТРОИТЕЛЬНЫЙ ПЛАН ЗЕМЕЛЬНОГО УЧАСТКА»?
Ответь только "ДА" или "НЕТ"."""

        try:
            client = ollama.Client(host=OLLAMA_URL, timeout=120.0)
            response = client.chat(
                model='qwen2.5vl:7b',
                messages=[{
                    'role': 'user',
                    'content': prompt,
                    'images': [img_data],
                }],
                stream=False,
                options={'temperature': 0.1},
            )

            result = response['message']['content'].strip().upper()
            print(f"  Ответ: {result}")

            if result == "ДА":
                title_page = page_num
                print(f"  Заголовок найден на странице {page_num + 1}")
                break
            else:
                print(f"  Нет заголовка на странице {page_num + 1}")

        except Exception as e:
            print(f"  Ошибка: {e}")
            continue

        time.sleep(0.5)

    if title_page is None:
        print("\  Заголовок ГПЗУ не найден ни на одной странице")
        doc.close()
        return None

    print(f"\  Ищем дату выдачи (после страницы {title_page + 1})...")

    for page_num in range(title_page, min(title_page + 5, len(doc))):
        print(f"\n  Проверяем страницу {page_num + 1} на наличие даты...")

        page = doc[page_num]
        pix = page.get_pixmap(dpi=300)
        img_data = pix.tobytes("png")

        prompt = """Найди на этой странице дату выдачи ГРАДОСТРОИТЕЛЬНОГО ПЛАНА ЗЕМЕЛЬНОГО УЧАСТКА.
Дата обычно выглядит как "Дата выдачи ДД.ММ.ГГГГ" или просто "ДД.ММ.ГГГГ".

Если нашел дату - напиши ТОЛЬКО дату в формате ДД.ММ.ГГГГ.
Если не нашел - напиши "НЕТ"."""

        try:
            client = ollama.Client(host=OLLAMA_URL, timeout=120.0)
            response = client.chat(
                model='qwen2.5vl:7b',
                messages=[{
                    'role': 'user',
                    'content': prompt,
                    'images': [img_data],
                }],
                stream=False,
                options={'temperature': 0.1},
            )

            result = response['message']['content'].strip()
            print(f"  Ответ: {result}")

            date_match = re.search(r'\d{2}\.\d{2}\.\d{4}', result)
            if date_match:
                date = date_match.group()
                print(f" Найдена дата выдачи на странице {page_num + 1}: {date}")

                with open('issuance_date.txt', 'w', encoding='utf-8') as f:
                    f.write(date)

                doc.close()
                return date

        except Exception as e:
            print(f"  Ошибка: {e}")
            continue

        time.sleep(0.5)

    print(f"\n  Заголовок найден на странице {title_page + 1}, но дата не найдена на следующих страницах")
    doc.close()
    return None
