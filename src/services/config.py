import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    # Основные настройки (существующие)
    ollama_url: str
    DOCUMENTS_PATH: str
    norms_model: str
    
    # Новые настройки для анализа чертежей (добавлены со значениями по умолчанию)
    drawing_vlm_model: str = "qwen3.6:latest"
    drawing_validation_model: str = "gemma3:27b"
    drawing_min_size_cm: float = 30.0

def load_config() -> Config:
    return Config(
        ollama_url=os.getenv("OLLAMA_BASE_URL", "http://ollama:11434"),
        DOCUMENTS_PATH=os.getenv("DOCUMENTS_PATH", "data/documents.json"),
        norms_model=os.getenv("NORMS_LLM_MODEL", "yandex/YandexGPT-5-Lite-8B-instruct-GGUF:latest"),
        
        # Чтение новых переменных окружения (если нет - берутся значения по умолчанию выше)
        drawing_vlm_model=os.getenv("DRAWING_VLM_MODEL", "qwen3.6:latest"),
        drawing_validation_model=os.getenv("DRAWING_VALIDATION_MODEL", "gemma3:27b"),
        drawing_min_size_cm=float(os.getenv("DRAWING_MIN_SIZE_CM", "30.0")),
    )