import os
import fitz  # PyMuPDF
from pathlib import Path
from .config import load_config

def detect_and_save_drawings(pdf_path: str, output_dir: str) -> list:
    """
    Сканирует PDF, находит страницы с размером большей стороны > DRAWING_MIN_SIZE_CM.
    Сохранает каждую такую страницу в отдельный PDF файл в папке output_dir/drawing_pages/.
    
    Возвращает список словарей:
    [{'page_num': 5, 'file_path': '/path/to/dw_page_005.pdf', 'size': '42.0x29.7cm'}, ...]
    """
    config = load_config()
    drawings_dir = Path(output_dir) / "drawing_pages"
    drawings_dir.mkdir(parents=True, exist_ok=True)
    
    drawing_pages_info = []
    
    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        print(f"🔍 Поиск чертежей в файле ({total_pages} стр.)... Критерий: > {config.drawing_min_size_cm} см")
        
        for i in range(total_pages):
            page = doc[i]
            # Получаем размеры в пунктах и конвертируем в см
            w_cm = page.rect.width * 2.54 / 72
            h_cm = page.rect.height * 2.54 / 72
            max_side = max(w_cm, h_cm)
            
            if max_side > config.drawing_min_size_cm:
                info = {
                    'page_num': i + 1,
                    'size': f"{w_cm:.1f}x{h_cm:.1f}cm",
                    'width_cm': w_cm,
                    'height_cm': h_cm
                }
                
                # Сохраняем страницу как отдельный PDF
                out_filename = f"dw_page_{i+1:03d}.pdf"
                out_pdf = drawings_dir / out_filename
                
                new_doc = fitz.open()
                new_doc.insert_pdf(doc, from_page=i, to_page=i)
                new_doc.save(str(out_pdf))
                new_doc.close()
                
                info['file_path'] = str(out_pdf)
                drawing_pages_info.append(info)
                print(f"   ✅ Стр. {i+1}: Чертеж ({info['size']}) -> {out_filename}")
        
        doc.close()
        
        if not drawing_pages_info:
            print("ℹ️ Чертежи не найдены (все страницы <= A4/A3)")
            
        return drawing_pages_info
        
    except Exception as e:
        print(f"❌ Ошибка при детекции чертежей: {e}")
        import traceback
        traceback.print_exc()
        return []