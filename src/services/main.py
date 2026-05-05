import os
import shutil
import uuid
from typing import Any, Dict, List
from pathlib import Path

# Импорт существующих модулей
from .gpuz import findDateInGPZU
from .json_saver import process_complex_json_to_csv
from .norms_actualizer import searchActualNorm, copyActualNorm
from .mopb_extractor import searchМОРВ
from .reference_parser import punktМОРВ
from .comparison import comparisionМОРВ

# Импорт новых модулей для чертежей
from . import drawings_detector
from . import drawings_analyzer

def process_mopb_validation(
    gpzu_pdf_path: str,
    mopb_pdf_path: str,
    output_dir: str = "outputs",
    perechen_xlsx: str = "data/Perechen.xlsx",
) -> Dict[str, Any]:
    """
    Основной пайплайн обработки:
    1. ГПЗУ -> Дата -> Актуализация норм.
    2. МОРВ -> Поиск ссылок -> Сравнение -> Отчеты.
    3. МОРВ -> Поиск чертежей -> Анализ VLM -> Отчет по чертежам.
    """
    
    # Генерация ID сессии и создание рабочей папки
    session_id = str(uuid.uuid4())
    session_output_dir = os.path.join(output_dir, session_id)
    os.makedirs(session_output_dir, exist_ok=True)
    
    print(f"🚀 Запуск обработки сессии: {session_id}")
    print(f"📂 Рабочая директория: {session_output_dir}")

    # ==========================================
    # ЭТАП 1: Обработка ГПЗУ и Актуализация норм
    # ==========================================
    print("\n--- ЭТАП 1: ГПЗУ и Нормы ---")
    extracted_date = findDateInGPZU(gpzu_pdf_path)
    print(f"📅 Извлеченная дата ГПЗУ: {extracted_date}")

    if extracted_date:
        # Передаем output_dir в функции, чтобы они писали результаты в папку сессии
        # Примечание: Если внутренние функции hardcode-ят пути, их тоже нужно будет править.
        # Здесь предполагается, что они используют относительные пути или глобальные переменные,
        # которые мы временно переопределяем или копируем файлы постфактум.
        
        # Для корректной работы в рамках сессии, лучше запускать функции из контекста папки сессии
        # или модифицировать сами функции. В данном варианте мы запускаем как есть, 
        # а затем собираем результаты.
        
        searchActualNorm(target_date=extracted_date, normsList=perechen_xlsx)
        copyActualNorm()
        
        # Перемещаем результаты актуализации в папку сессии (если они создались в корне/общей папке)
        # Предполагаем, что searchActualNorm создает 'все_СП_результаты.json' в текущей рабочей dir или norms/
        # Если файлы создаются в корне проекта, переместим их:
        src_norms_report = "все_СП_результаты.json" # Путь может отличаться в зависимости от реализации
        if os.path.exists(src_norms_report):
            dest_path = os.path.join(session_output_dir, "все_СП_результаты.json")
            shutil.move(src_norms_report, dest_path)
    else:
        print("⚠️ Дата не извлечена, этап актуализации пропущен.")

    # ==========================================
    # ЭТАП 2: Обработка МОРВ (Основной поток)
    # ==========================================
    print("\n--- ЭТАП 2: Анализ МОРВ (Нормы) ---")
    
    # Важно: Внутренние функции (searchМОРВ и др.) должны поддерживать передачу output_dir.
    # Если они жестко пишут в общие папки (MOPB_ссылки и т.д.), возникнет конфликт при параллельных запусках.
    # В данной реализации мы вызываем их, предполагая, что они работают с глобальными путями,
    # а затем забираем результаты. Для продакшена рекомендуется рефакторинг самих функций на прием output_dir.
    
    # Запуск пайплайна извлечения и сравнения
    searchМОРВ(MOPB_PDF=mopb_pdf_path) # Создает MOPB_ссылки/
    punktМОРВ()                         # Создает MOPB_ссылки_с_номерами/
    comparisionМОРВ()                   # Создает MOPB_сравнение/

    # Сбор основных результатов
    result_files: List[Dict[str, Any]] = []

    # 1. Файл со списком СП (если был создан)
    src_norms_report = "все_СП_результаты.json"
    if os.path.exists(src_norms_report):
        dest_path = os.path.join(session_output_dir, "все_СП_результаты.json")
        shutil.move(src_norms_report, dest_path)
        result_files.append({
            "path": dest_path,
            "filename": "все_СП_результаты.json",
            "size": os.path.getsize(dest_path),
            "type": "norms_list",
            "description": "Список актуальных норм"
        })

    # 2. CSV/Excel отчеты по сравнению норм
    # Функция process_complex_json_to_csv обычно берет из папки "MOPB_сравнение"
    try:
        generated_reports = process_complex_json_to_csv("MOPB_сравнение", session_output_dir)
        for file_path in generated_reports:
            if os.path.exists(file_path):
                result_files.append({
                    "path": file_path,
                    "filename": os.path.basename(file_path),
                    "size": os.path.getsize(file_path),
                    "type": "compliance_report",
                    "description": "Отчет соответствия нормам"
                })
    except Exception as e:
        print(f"⚠️ Ошибка при генерации CSV отчетов: {e}")

    # ==========================================
    # ЭТАП 3: Анализ чертежей (НОВЫЙ ФУНКЦИОНАЛ)
    # ==========================================
    print("\n--- ЭТАП 3: Анализ архитектурных чертежей ---")
    
    try:
        # Шаг 3.1: Детекция страниц с чертежами (по размеру)
        detected_drawings = drawings_detector.detect_and_save_drawings(
            pdf_path=mopb_pdf_path,
            output_dir=session_output_dir
        )
        
        if detected_drawings:
            print(f"✅ Найдено страниц с чертежами: {len(detected_drawings)}")
            
            # Шаг 3.2: Анализ через VLM и валидация
            drawing_report_path = drawings_analyzer.run_analysis(
                session_id=session_id,
                detected_drawings=detected_drawings,
                output_dir=session_output_dir
            )
            
            if drawing_report_path and os.path.exists(drawing_report_path):
                result_files.append({
                    "path": drawing_report_path,
                    "filename": os.path.basename(drawing_report_path),
                    "size": os.path.getsize(drawing_report_path),
                    "type": "drawings_report",
                    "description": "Анализ архитектурных чертежей (Qwen3.6)"
                })
                print("✅ Отчет по чертежам успешно добавлен в результаты.")
            else:
                print("⚠️ Анализ чертежей не вернул файл отчета.")
        else:
            print("ℹ️ Страницы формата А3+ не найдены, анализ чертежей пропущен.")
            
    except Exception as e:
        print(f"❌ Критическая ошибка в модуле анализа чертежей: {e}")
        import traceback
        traceback.print_exc()
        # Не прерываем общий процесс, ошибка только в доп. модуле

    # ==========================================
    # ФИНАЛ: Возврат результатов
    # ==========================================
    print(f"\n🏁 Обработка сессии {session_id} завершена.")
    print(f"📦 Всего файлов в результате: {len(result_files)}")
    
    return {
        "sessionId": session_id,
        "extracted_date": extracted_date,
        "output_directory": session_output_dir,
        "files": result_files,
        "status": "completed"
    }