from __future__ import annotations
import json
import os
import shutil
import threading
import traceback
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
import re

from .gpuz import findDateInGPZU
from .norms_actualizer import searchActualNorm, copyActualNorm
from .mopb_extractor import searchМОРВ
from .reference_parser import punktМОРВ
from .comparison import comparisionМОРВ
from .json_saver import process_complex_json_to_xlsx, json_to_excel_all_docs


_processing_lock = threading.Lock()


class SessionManager:
    """Управление сессиями обработки ГПЗУ/МОРВ."""

    def __init__(
        self,
        upload_folder: str,
        output_folder: str,
        sessions_file: str,
        perechen_pdf: str,
    ):
        self.upload_folder = upload_folder
        self.output_folder = output_folder
        self.sessions_file = sessions_file
        self.perechen_pdf = perechen_pdf
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._state_lock = threading.Lock()
        self._load()

    # ----- персистентность -----
    def _load(self) -> None:
        if os.path.exists(self.sessions_file):
            try:
                with open(self.sessions_file, "r", encoding="utf-8") as f:
                    self._sessions = json.load(f)
            except Exception:
                self._sessions = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.sessions_file) or ".", exist_ok=True)
        tmp = self.sessions_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._sessions, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.sessions_file)

    # ----- базовые операции -----
    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._state_lock:
            items = [dict(s) for s in self._sessions.values()]
        items_correct = []
        for item in items:
            if item.get('sessionId', ''):
                items_correct.append(item)
        items_correct.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        for s in items_correct:
            self._decorate_files(s)
        return items_correct

    def get(self, sessionId: str) -> Optional[Dict[str, Any]]:
        with self._state_lock:
            s = self._sessions.get(sessionId)
            if not s:
                return None
            s = dict(s)
        self._decorate_files(s)
        return s

    def _update(self, sessionId: str, **fields: Any) -> None:
        with self._state_lock:
            s = self._sessions.setdefault(sessionId, {})
            s.update(fields)
            self._save()

    def delete(self, sessionId: str) -> bool:
        with self._state_lock:
            s = self._sessions.pop(sessionId, None)
            if not s:
                return False
            self._save()

        session_dir = os.path.join(self.output_folder, sessionId)
        if os.path.isdir(session_dir):
            shutil.rmtree(session_dir, ignore_errors=True)
        for p in (s.get("first_file_path"), s.get("second_file_path")):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return True

    # ----- создание файлов и запуск обработки -----
    def start_first(self, original_name: str, saved_path: str) -> Dict[str, Any]:
        """Запускает фоновую обработку ГПЗУ: поиск даты + актуализация норм."""
        sessionId = str(uuid.uuid4())
        session = {
            "sessionId": sessionId,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "status": "processing_norms",
            "first_file_name": original_name,
            "first_file_path": saved_path,
            "second_file_name": None,
            "second_file_path": None,
            "extracted_date": None,
            "files": [],
            "error": None,
        }
        with self._state_lock:
            self._sessions[sessionId] = session
            self._save()

        # Дата извлекается быстрым текстовым поиском - синхронно;
        try:
            extracted_date = findDateInGPZU(saved_path)
        except Exception as exc:
            self._update(sessionId, status="error", error=f"GPZU: {exc}")
            raise
        self._update(sessionId, extracted_date=extracted_date)

        threading.Thread(
            target=self._process_norms_bg,
            args=(sessionId, extracted_date),
            daemon=True,
        ).start()

        return {
            "sessionId": sessionId,
            "extracted_date": extracted_date,
            "status": "processing_norms",
            "message": "Файл принят. Начинаем актуализацию нормативной базы…",
        }

    def start_second(
        self, sessionId: str, original_name: str, saved_path: str
    ) -> Dict[str, Any]:
        """Запускает фоновую обработку проектной документации."""
        with self._state_lock:
            s = self._sessions.get(sessionId)
            if not s:
                raise KeyError("Сессия не найдена")
            if s["status"] != "awaiting_second":
                raise RuntimeError(
                    f"Сессия в статусе '{s['status']}' - нельзя начать обработку МОРВ"
                )
            s["second_file_name"] = original_name
            s["second_file_path"] = saved_path
            s["status"] = "processing_second"
            self._save()

        threading.Thread(
            target=self._process_mopb_bg,
            args=(sessionId, saved_path),
            daemon=True,
        ).start()

        return {"sessionId": sessionId, "status": "processing_second"}

    def restore(self, sessionId: str) -> Dict[str, Any]:
        s = self.get(sessionId)
        if not s:
            raise KeyError("Сессия не найдена")
        return {
            "sessionId": s["sessionId"],
            "extracted_date": s.get("extracted_date"),
            "first_file_name": s.get("first_file_name"),
            "status": s.get("status"),
        }

    # ----- фоновые задачи -----
    def _process_norms_bg(self, sessionId: str, extracted_date: str) -> None:
        try:
            with _processing_lock:
                searchActualNorm(
                    target_date=extracted_date, normsList=self.perechen_pdf
                )
                copyActualNorm()

                session_dir = os.path.join(self.output_folder, sessionId)
                os.makedirs(session_dir, exist_ok=True)
                json_to_excel_all_docs("все_СП_результаты.json", "все_СП_результаты.xlsx")
                norms_result = "все_СП_результаты.xlsx"
                if os.path.exists(norms_result):
                    dest = os.path.join(session_dir, norms_result)
                    shutil.copy(norms_result, dest)
                    with self._state_lock:
                        self._sessions[sessionId].setdefault("files", []).append(
                            {
                                "path": dest,
                                "filename": norms_result,
                                "size": os.path.getsize(dest),
                            }
                        )
                        self._save()

            self._update(sessionId, status="awaiting_second")
        except Exception as exc:
            traceback.print_exc()
            self._update(sessionId, status="error", error=str(exc))

    def _process_mopb_bg(self, sessionId: str, mopb_path: str) -> None:
        try:
            with _processing_lock:
                searchМОРВ(MOPB_PDF=mopb_path)
                punktМОРВ()
                comparisionМОРВ()

                session_dir = os.path.join(self.output_folder, sessionId)
                os.makedirs(session_dir, exist_ok=True)

                new_files = []
                for file_path in process_complex_json_to_xlsx(
                    "MOPB_сравнение", session_dir
                ):
                    new_files.append(
                        {
                            "path": file_path,
                            "filename": os.path.basename(file_path),
                            "size": os.path.getsize(file_path),
                        }
                    )
            # сортировка файлов по прицнипу ФЗ -> СП -> ГОСТ -> ПП

            new_files_sorted = sorted(new_files, key=self.get_sort_key)

            with self._state_lock:
                files = self._sessions[sessionId].setdefault("files", [])
                existing = {f["filename"] for f in files}
                for f in new_files_sorted:
                    if f["filename"] not in existing:
                        files.append(f)
                self._sessions[sessionId]["status"] = "completed"
                self._save()
        except Exception as exc:
            traceback.print_exc()
            self._update(sessionId, status="error", error=str(exc))

    # ----- утилиты -----
    def _decorate_files(self, session: Dict[str, Any]) -> None:
        """Добавляет download_url каждому файлу для фронтенда."""
        sid = session.get("sessionId")
        for f in session.get("files", []):
            f["download_url"] = f"/fire/api/session/{sid}/download/{f['filename']}"

    def file_path(self, sessionId: str, filename: str) -> Optional[str]:
        s = self.get(sessionId)
        if not s:
            return None
        for f in s.get("files", []):
            if f["filename"] == filename:
                return f["path"]
        return None

    def session_dir(self, sessionId: str) -> str:
        return os.path.join(self.output_folder, sessionId)
    
    def natural_sort_key(self, text):
        """
        Преобразует строку в кортеж, где числа становятся int для натуральной сортировки
        """
        parts = re.split(r'(\d+)', text)
        key = []
        for part in parts:
            if part.isdigit():
                key.append(int(part))
            else:
                key.append(part)
        return tuple(key)

    def get_sort_key(self, item):
        filename = item["filename"]

        type_priority = {"ФЗ": 0, "СП": 1, "ГОСТ": 2, "ПП": 3}
        
        # Определяем тип документа
        if "_ФЗ" in filename:
            doc_type = "ФЗ"
            # Извлекаем номер и используем natural_sort_key для всей строки
            return (type_priority[doc_type], self.natural_sort_key(filename))
        
        elif filename.startswith("СП"):
            doc_type = "СП"
            # Используем natural_sort_key для всей строки
            return (type_priority[doc_type], self.natural_sort_key(filename))
        
        elif filename.startswith("ГОСТ"):
            doc_type = "ГОСТ"
            return (type_priority[doc_type], self.natural_sort_key(filename))
        
        elif filename.startswith("ПП"):
            doc_type = "ПП"
            return (type_priority[doc_type], self.natural_sort_key(filename))
        
        else:
            return (999, self.natural_sort_key(filename))

