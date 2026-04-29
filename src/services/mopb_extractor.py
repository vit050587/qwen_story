import os
import json
import ollama
import fitz
import re
import copy

from .config import load_config
from .documents_registry import is_fz_document, load_documents

OLLAMA_URL = load_config().ollama_url
NORMS_MODEL = load_config().norms_model

# ======== 4 ЭТАП ==========
def searchМОРВ(MOPB_PDF):
    # ИЩЕМ ПРОСТО ВСЕ ССЫЛКИ НА СП и СТАТЬИ ЗАКОНА, ПОКА БЕЗ ВЫДЕЛЕНИЯ КОНКРЕТНОГО НОМЕРА ПУНКТА
    # ВЫДЕЛЕНИЕ НОМЕРА ПУНКТА БУДЕТ НА СЛЕДУЮЩЕМ ЭТАПЕ
    print("  ДВУХЭТАПНЫЙ АНАЛИЗ ДОКУМЕНТА MOPB.pdf")

    # ======== 4 ЭТАП ==========
    OUTPUT_FOLDER = "MOPB_ссылки"

 
    DOCUMENTS = load_documents()
     
    results = []
    
    pages_text = _extract_text_from_MOPB(MOPB_PDF)

    for doc in DOCUMENTS:
        result = _process_document(MOPB_PDF, doc, pages_text, OUTPUT_FOLDER)
        if result:
            results.append(result)
    
    print(" ИТОГОВАЯ СТАТИСТИКА")
    
    for r in results:
        print(f"\n{r['doc_code']}: {r['references_count']} ссылок")
        print(f"     {r['json_file']}")
    
     
    print("  РАБОТА ЗАВЕРШЕНА")

def _process_document(MOPB_PDF, doc, doc_text, output_folder):
    
    print(f"🏁 ОБРАБОТКА {doc['code']}")
     
    os.makedirs(output_folder, exist_ok=True)
     
    doc_safe = doc['code'].replace(' ', '_').replace('-', '_')
    output_pdf = os.path.join(output_folder, f"MOPB_{doc_safe}_страницы.pdf")
    output_json = os.path.join(output_folder, f"MOPB_{doc_safe}_пункты_полные.json")
    output_txt = os.path.join(output_folder, f"MOPB_{doc_safe}_абзацы.txt")

    # составляем альтернативные варианты как указанные + код с отсутсвующими пробелами + переносы строк
    alternatives = [doc["code"]] + doc["codes"] + [doc['code'].replace(" ", "")] + [doc['code'].replace(" ", "\n")] + [doc['code'].replace(" ", " \n")] 
     
    matched_page_numbers, merged_pages_text = _extract_pages_with_doc_mention(MOPB_PDF, doc_text, doc['code'], output_pdf, alternatives = alternatives)
    
    if not matched_page_numbers:
        print(f"\n Страницы с упоминанием {doc['code']} не найдены. Пропускаем.")
        return None
    
    # Используем склеенный текст страниц, чтобы LLM видел целые абзацы.
    merged_pages_text = [{"text":p, "page_num":i+1} for i, p in enumerate(merged_pages_text) if i+1 in matched_page_numbers]
    
    print(f"  ЭТАП 2: ПОИСК ССЫЛОК ЧЕРЕЗ LLM")
     
    all_references = []
    
    for page_data in merged_pages_text:
        print(f"\n Анализ страницы {page_data['page_num']}...")
        
        references = _extract_references_with_llm(
            page_data['text'], 
            page_data['page_num'], 
            doc['code'],
            doc['pattern']
        )
        
        for ref in references:
            #Дополнительная проверка для сводов правил СП
            if is_fz_document(doc['code']) or (doc['code'] in ref['punkt']) or (str(doc['code']).replace(" ","") in ref['punkt']) or any(alternative in ref['punkt'] for alternative in alternatives):
                ref['page'] = page_data['page_num']
                ref['doc_code'] = doc['code']
            
                all_references.append(ref)
                print(f"    Найдена ссылка на {ref['punkt']}")
            else:
                print(f"    Найденная ссылка на {doc['code']} в пункте {ref['punkt']} некорректна")
    
    print(f"Всего найдено ссылок: {len(all_references)}")
     
    output = {
        'doc_code': doc['code'],
        'doc_name': doc['name'],
        'extracted_pages': matched_page_numbers,
        'references': all_references
    }
    
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    
     
    with open(output_txt, 'w', encoding='utf-8') as f:
        for i, ref in enumerate(all_references, 1):
            f.write(f"\n{'='*80}\n")
            f.write(f"{i}. {doc['code']} ссылка: {ref['punkt']}\n")
            f.write(f"   Страница: {ref['page']}\n")
            f.write(f"{'='*80}\n")
            f.write(ref['full_paragraph'])
            f.write("\n")
    
    print(f"\n Результаты сохранены в {output_folder}")
    
    return {
        'doc_code': doc['code'],
        'references_count': len(all_references),
        'json_file': output_json,
        'txt_file': output_txt
    }

def _extract_references_with_llm(page_text, page_num, doc_code, doc_pattern):
   
    if 'СП' not in doc_code:
        prompt = f"""Ты - эксперт по проектной документации. Проанализируй текст страницы и найди ВСЕ ссылки на {doc_code}.

Текст страницы {page_num}:
====================
{page_text}
====================

Задача:
1. Найди все строки, где есть ссылки на {doc_code}
2. Для каждой ссылки определи:
   - полную строку ссылки
   - полный абзац, в котором находится эта ссылка

Примеры ссылок на Федеральный закон {doc_code}:
- "ст. 6"
- "ч.5 ст.134 № {doc_code}"
- "согласно требованиям ст. 90 Технического регламента {doc_code}"
- "ч. 4 ст. 89 ФЗ №123"
- "в соответствии с требованиями {doc_code}"

ВАЖНО: Игнорируй ссылки на другие документы!

Верни результат в формате JSON:
[
    {{
        "punkt": "полная строка ссылки",
        "full_paragraph": "полный текст абзаца со ссылкой"
    }}
]

Если ссылок нет - верни пустой массив [].
Верни ТОЛЬКО JSON, без пояснений.
"""
    else:
        excluded_documents = "СП 1.13130, СП 2.13130, СП 3.13130, СП 4.13130, СП 59.13330, 123-ФЗ и другие"
        excluded_documents = excluded_documents.replace(doc_code, "")
        excluded_documents = excluded_documents.replace(", ,",",")
        
        prompt = f"""Ты - эксперт по проектной документации. Проанализируй текст страницы и найди ВСЕ ссылки ТОЛЬКО на {doc_code}.

Текст страницы {page_num}:
====================
{page_text}
====================

Задача:
1. Найди все строки, где есть ссылки на {doc_code}
2. Игнорируй ссылки на другие документы ({excluded_documents})
3. Для каждой ссылки определи:
   - полную строку ссылки
   - полный абзац, в котором находится эта ссылка

Примеры ссылок на {doc_code}:
- "8.9 {doc_code}"
- "п.5.1.3 {doc_code}"
- "п. 3.1 {doc_code}"
- "табл. 1 {doc_code}"
- "пп. 4.4.15 {doc_code}"

ВАЖНО: Если в строке есть упоминание другого документа - ПРОПУСТИ такую ссылку!

Верни результат в формате JSON:
[
    {{
        "punkt": "полная строка ссылки",
        "full_paragraph": "полный текст абзаца со ссылкой"
    }}
]

Если ссылок нет - верни пустой массив [].
Верни ТОЛЬКО JSON, без пояснений.
"""
    
    try:
        client = ollama.Client(host=OLLAMA_URL, timeout=1200.0)
        response = client.chat(
            model=NORMS_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            stream=False,
            options={'temperature': 0.1, 'num_predict': 8192}
        )
        
        result_text = response['message']['content'].strip()
        
        # Ищем JSON в ответе
        json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        else:
            return []
            
    except Exception as e:
        print(f"     Ошибка LLM: {e}")
        return []

def _extract_page_text_with_llm(page):
    pix = page.get_pixmap(dpi=200)
    img_data = pix.tobytes("png")
    prompt = """Проверь, что на этой странице есть текст. Если он есть, то верни его содержание. Если его нет, то верни пустую строку.
    Верни только ответ без пояснений"""

    try:
        client = ollama.Client(host=OLLAMA_URL, timeout=300.0)
        response = client.chat(
            model='qwen2.5vl:7b',
            messages=[{
                'role': 'user',
                'content': prompt,
                'images': [img_data]
            }],
            stream=False,
            options={'temperature': 0.1}
        )
        
        result = response['message']['content'].strip()
        return result
    except Exception as e:
        print(f"  Ошибка: {e}")
        return ""

def _extract_text_from_MOPB(pdf_path, output_pdf='MOPB_распознанный'):
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    print(f" Всего страниц в исходном PDF: {total_pages}")
    pages_text = []

    for i, p in enumerate(doc):
        page_text = p.get_text()
        if len(page_text) >= 100:
            pages_text.append(page_text)
        else:
            pages_text.append("")
            print(f'На странице {i+1} не удалось прочитать текст. На странице расположено изображение')
    
    return pages_text


def _extract_pages_with_doc_mention(pdf_path, pages_text, doc_code, output_pdf, alternatives:list = []):
    print(f"  ЭТАП 1: ПОИСК СТРАНИЦ С УПОМИНАНИЕМ {doc_code}")

    doc = fitz.open(pdf_path)
    # Удаляем пустые строки из текста для облегчения работы модели (особенно в районе проектной рамки)
    new_pages_text = []
    for txt in pages_text:
        lines = txt.splitlines()
        new_lines = ""
        for line in lines:
            match = re.search(r"^\s*$", line)
            if not match:
                new_lines += line + "\n"
        new_pages_text.append(new_lines)
    merged_pages_text = _merge_pages_by_center_split(new_pages_text)

    total_pages = len(pages_text)
    pages_with_doc = []
    
    for page_num in range(total_pages):
        merged_text = merged_pages_text[page_num]
        
        if len(alternatives) == 0:
            if (doc_code in merged_text)or(str(doc_code).replace(" ","") in merged_text):
                pages_with_doc.append(page_num + 1)
                print(f"    Страница {page_num + 1}: найдено '{doc_code}'")
        else:
            for alternative in alternatives:
                if alternative in merged_text:
                    pages_with_doc.append(page_num + 1)
                    print(f"    Страница {page_num + 1}: найдено '{alternative}'")
                    break
    
    print(f"\n Найдено страниц с упоминанием {doc_code}: {len(pages_with_doc)}")
    print(f"   Страницы: {pages_with_doc}")
    
    if pages_with_doc:
        print(f"\n📄 Создание PDF с {len(pages_with_doc)} страницами...")
        new_doc = fitz.open()
        
        for page_num in pages_with_doc:
            new_doc.insert_pdf(doc, from_page=page_num-1, to_page=page_num-1)
        
        new_doc.save(output_pdf)
        new_doc.close()
        
        file_size = os.path.getsize(output_pdf) / 1024
        print(f"  Создан файл: {output_pdf} ({file_size:.2f} KB)")
    else:
        print(f"\n Страницы с упоминанием {doc_code} не найдены")
    
    return pages_with_doc, merged_pages_text


def _merge_pages_by_center_split(pages_text_list):
    """
    Разделяет страницы пополам по абзацу и склеивает попарно разделенные части
    """
    pages_text_list = copy.deepcopy(pages_text_list)

    def _find_split_position_from_center(text: str) -> int | None:
        """
        Ищет ближайшую к центру позицию, где встречается:
            '\n' + заглавная буква, кроме первых символов 'СП'
            '\n' + нумерация пунктов кириллицей (например д.1), кроме п. (ссылка на пункт документа)
            '\n' + цифра, кроме обозначений пунктов нормативных документов и предыдущая строка не заканчивается на'СП'
        Возвращает индекс начала '\n', либо None.
        """

        if not text:
            return None

        #pattern = re.compile(r"\n(?=[A-ZА-ЯЁ0-9])")
        pattern = re.compile(r"\n(?=[A-ZА-РТ-ЯЁ])|\n(?=С[^П])|\n(?=[а-ор-я]\.[1-9]+)|(?<!СП\s)\n(?=[1-9](?![\.0-9]*\s+СП))")
        middle = len(text) // 2

        # Собираем все позиции начала совпадений
        matches = [m.start() for m in pattern.finditer(text)]
        if not matches:
            return None

        # Ищем ближайшую к середине.
        best_pos = min(matches, key=lambda pos: abs(pos - middle))
        return best_pos

    for i in range(len(pages_text_list) - 1):
        next_page_text = pages_text_list[i + 1]
        split_pos = _find_split_position_from_center(next_page_text)

        if split_pos is None:
            continue

        part_to_move = next_page_text[:split_pos]
        remaining_part = next_page_text[split_pos:]

        pages_text_list[i] += part_to_move
        pages_text_list[i + 1] = remaining_part

    return pages_text_list
