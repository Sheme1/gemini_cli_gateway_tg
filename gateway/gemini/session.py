import asyncio
import logging
import json
import re
from typing import Callable

from gateway.config import Config
from gateway.gemini.parser import GeminiStreamParser

logger = logging.getLogger(__name__)


class SessionManager:
    """Управляет сессиями Gemini CLI."""

    def __init__(self, config: Config):
        self.config = config
        # user_id -> gemini_session_id
        self.active_sessions: dict[int, str] = {}
        self.is_alive_process = False

    async def get_sessions_list(self) -> list[tuple[str, str]]:
        """Возвращает актуальный список сессий: список кортежей (id, описание)."""
        process = await asyncio.create_subprocess_exec(
            "gemini", "--list-sessions",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.gemini_working_dir
        )
        stdout, _ = await process.communicate()
        lines = stdout.decode("utf-8").splitlines()
        
        sessions = []
        for line in lines:
            line = line.strip()
            # Пропускаем логи и пустые строки
            if (not line or "No previous" in line or "Keychain" in line 
                or "Loaded" in line or "Using" in line):
                continue
                
            # Формат вывода --list-sessions обычно содержит индекс/ID и текст
            match = re.search(r"^\s*([a-zA-Z0-9\-]+)\s+(.+)$", line)
            if match:
                session_id = match.group(1)
                desc = match.group(2)[:60] + "..." if len(match.group(2)) > 60 else match.group(2)
                sessions.append((session_id, desc))
            else:
                parts = line.split(maxsplit=1)
                if len(parts) >= 1:
                    session_id = parts[0]
                    desc = parts[1][:60] + "..." if len(parts) > 1 else "Без описания"
                    sessions.append((session_id, desc))
        return sessions

    async def is_alive(self) -> bool:
        # Для совместимости с handlers, которые ожидают этот метод.
        # Поскольку процессы теперь одноразовые, всегда возвращаем True,
        # так как сервис концептуально "жив" и готов к запросам.
        return True

    async def kill(self) -> None:
        # Для совместимости. Теперь процессы убиваются сами.
        pass

    async def reset(self, user_id: int) -> None:
        """Сброс контекста (/new): очистка привязанного session_id."""
        if user_id in self.active_sessions:
            del self.active_sessions[user_id]
            logger.info(f"Cleared session context for user {user_id}")

    async def set_active_session(self, user_id: int, session_id: str) -> None:
        self.active_sessions[user_id] = session_id
        logger.info(f"Set active session {session_id} for user {user_id}")

    async def send_prompt(
        self,
        prompt: str,
        user_id: int,
        on_chunk: Callable[[str], asyncio.Future],
        on_approval: Callable[[dict], asyncio.Future],
    ) -> None:
        """Отправить промпт в одноразовый процесс и стримить ответ."""
        args = [
            "gemini",
            "-m",
            self.config.gemini_model,
            "-o",
            "stream-json",
            "-p",
            prompt,
        ]
        args.extend(self.config.approval_mode_flag)
        args.extend(self.config.sandbox_flag)

        session_id = self.active_sessions.get(user_id)
        if session_id:
            args.extend(["--resume", session_id])

        logger.info(f"Spawning Gemini: {' '.join(args)}")

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.gemini_working_dir,
        )

        while True:
            line = await process.stdout.readline()
            if not line:
                break

            line_text = line.decode("utf-8").strip()
            
            # Попытка парсинга sessionId на лету для новых сессий 
            # (если мы не задавали --resume, CLI создаст новую сессию и вернет метаданные)
            try:
                data = json.loads(line_text)
                if "sessionId" in data and not session_id:
                    # Захватываем новосозданный sessionId
                    self.active_sessions[user_id] = data["sessionId"]
                    logger.info(f"Captured new session ID: {data['sessionId']} for user {user_id}")
            except Exception:
                pass

            event = GeminiStreamParser.parse_line(line_text)

            if event.text_chunk:
                await on_chunk(event.text_chunk)

            if event.approval_request:
                await on_approval(event.approval_request)
                # Headless процесс при запросе интерактивного подтверждения просто прерывается.
                # Если нужна сложная обработка аппрувов - нужен полноценный daemon или TTY.
                break

        await process.wait()

    async def answer_approval(self, answer: str) -> None:
        # Заглушка. Headless режим не поддерживает интерактивный ввод.
        # Следует использовать --yolo.
        pass

